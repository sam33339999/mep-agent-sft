"""Real-browser test of the review UI and the Three.js viewer (clicks, typing, mouse drag).

Skipped when playwright or a Chrome binary is missing. Run: uv sync --extra e2e && uv run pytest tests/test_e2e_ui.py
"""

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

CHROME = shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium binary")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    db = tmp_path_factory.mktemp("e2e") / "review.db"
    proc = subprocess.Popen(
        [sys.executable, "-m", "mep_sft", "serve", "--db", str(db), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/stats", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def page(base):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
                  "--ignore-gpu-blocklist"],
        )
        pg = browser.new_page(viewport={"width": 1500, "height": 950})
        pg.set_default_timeout(30000)
        yield pg
        browser.close()


def _toast(page):
    return page.inner_text("#toast")


def test_generate_open_explain_step_and_approve(base, page):
    page.goto(base)
    page.fill("#gn", "6")
    page.select_option("#gd", "2")
    page.click("#gen")
    page.wait_for_selector(".item")
    assert page.locator(".item").count() == 6

    # open the first sample that actually has violations
    page.locator(".item", has_text="碰撞").first.click()
    page.wait_for_selector("#fig.js-plotly-plot")
    assert "待審" in page.inner_text("h2")

    page.locator("li.vi").first.click()  # explanation card
    page.wait_for_function("document.querySelector('#explain').innerText.includes('怎麼算')")
    assert "圖上怎麼看" in page.inner_text("#explain")

    page.get_by_role("button", name="下一步 ▶").click()  # step-by-step tour
    page.wait_for_function("document.querySelector('.steps .meta').innerText.includes('步驟 1')")

    page.get_by_role("button", name="核准", exact=True).click()
    page.wait_for_function("document.querySelector('h2').innerText.includes('已核准')")
    page.wait_for_function("document.querySelector('#stats').innerText.includes('核准 1')")

    page.get_by_role("button", name="已核准").click()  # approved tab lists it
    page.wait_for_function("document.querySelectorAll('.item').length === 1")


def test_edit_makes_new_version_and_withdraws_approval(base, page):
    page.goto(base)
    page.get_by_role("button", name="已核准").click()
    page.locator(".item").first.click()
    page.wait_for_selector("#pipes")
    pipes = json.loads(page.input_value("#pipes"))
    pipes[0]["points"][0][2] = round(pipes[0]["points"][0][2] + 0.05, 4)
    page.fill("#pipes", json.dumps(pipes))
    page.get_by_role("button", name="驗證並預覽").click()
    page.wait_for_function("document.querySelector('#after').innerText.length > 0")
    page.get_by_role("button", name="存成新版本").click()
    page.wait_for_function("document.querySelector('h2').innerText.includes('待審')")
    assert "v2" in page.inner_text("main .muted")
    assert "human" in page.inner_text("main .muted")


def test_invalid_edit_cannot_be_approved_without_override(base, page):
    items = json.loads(page.request.get(base + "/api/samples?status=pending").text())
    target = next(i for i in items if i["variant"] != "detect_only" and i["issue_types"])
    page.goto(f"{base}/#{target['id']}")
    page.wait_for_function(f"document.querySelector('h2')?.innerText.startsWith('{target['id']}')")
    page.wait_for_selector("#pipes")
    pipes = json.loads(page.input_value("#pipes"))
    for q in pipes[0]["points"]:
        q[2] = 2.0  # below the clear-height line
    page.fill("#pipes", json.dumps(pipes))
    nxt = json.loads(page.request.get(f"{base}/api/samples/{target['id']}").text())["current"]["version"] + 1
    page.get_by_role("button", name="存成新版本").click()
    for _ in range(300):
        head, toast = page.inner_text("main .muted"), page.inner_text("#toast")
        if f"v{nxt}" in head or "失敗" in toast:
            break
        page.wait_for_timeout(100)
    assert f"v{nxt}" in head, f"save did not produce v{nxt}: header={head!r} toast={toast!r}"
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.get_by_role("button", name="核准", exact=True).click()
    for _ in range(100):  # the dialog appears after the 409 comes back
        if asked:
            break
        page.wait_for_timeout(100)  # (not time.sleep: only Playwright calls pump dialog events)
    assert asked and "未通過" in asked[0]
    assert "待審" in page.inner_text("h2")


def test_quiz_mode_hides_answers_until_reveal(base, page):
    page.goto(base)
    page.get_by_role("button", name="待審").click()
    page.check("#quiz")
    page.wait_for_function(  # the list re-renders without the answer chips
        "document.querySelectorAll('.item').length > 0 && "
        "![...document.querySelectorAll('.chip')].some(c => /碰撞|穿牆|淨高|坡度|無違規/.test(c.textContent))")
    page.locator(".item").first.click()
    page.wait_for_selector(".quizbox")
    text = page.inner_text("main")
    assert "違規" not in page.inner_text("#fig")
    assert "測驗模式" in text
    page.get_by_role("button", name="揭曉答案").click()
    page.wait_for_selector(".banner")
    assert "正確答案" in page.inner_text(".banner")


def test_export_contains_only_approved(base, page):
    body = page.request.get(base + "/api/export").text()
    lines = [json.loads(x) for x in body.splitlines()]
    assert lines == [] or all(r["meta"]["source"] in ("auto", "human") for r in lines)


def test_viewer_orbits_with_mouse_and_slider(base, page):
    sid = json.loads(page.request.get(base + "/api/samples?difficulty=2").text())[0]["id"]
    page.goto(f"{base}/viewer?id={sid}")
    page.wait_for_function("window.__viewer && window.__viewer.ready()")
    time.sleep(1.0)
    before = page.screenshot()
    box = page.locator("canvas").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 + 150
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 250, cy + 40, steps=12)
    page.mouse.up()
    time.sleep(0.8)
    rotated = page.screenshot()
    assert before != rotated  # orbit controls responded to a real drag
    page.locator("#t").fill("1")  # slider -> after state
    time.sleep(0.5)
    assert page.screenshot() != rotated
    if page.locator(".v").count():
        page.locator(".v").first.click()
        assert "open" in page.locator(".v").first.get_attribute("class")
