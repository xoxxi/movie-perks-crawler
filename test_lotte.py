from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://www.cgv.co.kr/culture-event/")
    page.wait_for_timeout(5000)
    page.click("text=이벤트/혜택")
    page.wait_for_timeout(3000)
    print("현재 URL:", page.url)
    text = page.evaluate("document.body.innerText")
    print(text[:2000])
    page.screenshot(path="cgv_event_screenshot.png", full_page=True)
    browser.close()