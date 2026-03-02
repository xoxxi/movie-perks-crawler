from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://www.megabox.co.kr/event/movie")
    page.wait_for_timeout(8000)
    
    # data-no 속성으로 이벤트 카드 찾기
    items = page.evaluate("""
        () => {
            const cards = document.querySelectorAll('[data-no], [onclick], .event-item, .eventBtn');
            return Array.from(cards).slice(0, 10).map(el => ({
                tag: el.tagName,
                dataNo: el.getAttribute('data-no'),
                onclick: el.getAttribute('onclick'),
                text: el.innerText.trim().slice(0, 50),
                className: el.className
            }));
        }
    """)
    for item in items:
        print(item)
    
    browser.close()