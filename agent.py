"""
movie-perks-crawler Agent v2.0 (LangGraph)
- LangGraph로 구현한 AI 에이전트
- 노드: 크롤링 → 분석 → 검증 → 재시도/저장 → 알림
- 특전 0개면 AI가 스스로 프롬프트 바꿔서 재시도
"""

import os
import json
import re
import urllib.request
from typing import TypedDict
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from supabase import create_client

load_dotenv()

# ──────────────────────────────────────────
# 환경변수
# ──────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY 가 .env 에 없습니다.")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("SUPABASE_URL 또는 SUPABASE_ANON_KEY 가 .env 에 없습니다.")

llm      = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)

print(f"SERVICE_KEY 앞 20자: {SUPABASE_SERVICE_KEY[:20] if SUPABASE_SERVICE_KEY else 'None'}")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

TARGET_URLS = [
    {"url": "https://www.megabox.co.kr/event/movie", "chain": "메가박스", "type": "megabox"},
    {"url": "https://www.cgv.co.kr/culture-event/", "chain": "CGV", "type": "normal"},
    {"url": "https://www.lottecinema.co.kr/NLCHS/Event/DetailList?code=20", "chain": "롯데시네마", "type": "lotte"},
]
PROMPT_STRATEGIES = [
    {
        "name": "기본",
        "instruction": """너는 영화 특전 정보 추출 전문가야.
아래 텍스트에서 특전 이벤트를 찾아서 JSON으로 반환해줘.

benefit_type 규칙 (반드시 아래 중 하나만 사용):
- 텍스트에 "포스터" 포함 → "포스터"
- 텍스트에 "포토카드" 포함 → "포토카드"
- 텍스트에 "필름마크" 포함 → "필름마크"
- 텍스트에 "엽서" 포함 → "엽서"
- 텍스트에 "스티커" 포함 → "스티커"
- 텍스트에 "키링" 포함 → "키링"
- 텍스트에 "굿즈" 포함 → "굿즈"
- 그 외 실물 증정 → "기타"

week 규칙:
- "개봉 1주차", "개봉주" 등 → 숫자만 (예: 1)
- 주차 정보 없으면 → null

제외: 할인/쿠폰 이벤트, 포토존만 있는 이벤트, 종료된 이벤트""",
    },
    {
        "name": "관대",
        "instruction": """너는 영화 특전 정보 추출 전문가야.
아래 텍스트에서 실물 증정이 있는 모든 이벤트를 찾아줘.

benefit_type 규칙 (반드시 아래 중 하나만 사용):
- "포스터" → "포스터"
- "포토카드" → "포토카드"
- "필름마크" → "필름마크"
- "엽서" → "엽서"
- "스티커" → "스티커"
- "키링" → "키링"
- "굿즈", "MD", "기념품", "패키지" → "굿즈"
- 그 외 실물 증정 → "기타"

추첨 경품도 포함. 순수 할인/쿠폰만 있는 이벤트는 제외.""",
    },
    {
        "name": "최대관대",
        "instruction": """너는 영화 이벤트 정보 추출 전문가야.
영화 관련 모든 이벤트를 최대한 넓게 추출해줘.

benefit_type 규칙 (반드시 아래 중 하나만 사용):
- "포스터" → "포스터"
- "포토카드" → "포토카드"
- "필름마크" → "필름마크"
- "엽서" → "엽서"
- "스티커" → "스티커"
- "키링" → "키링"
- "굿즈" → "굿즈"
- 상영회, 무대인사 → "상영회"
- 그 외 → "기타""",
    },
]


# ──────────────────────────────────────────
# 에이전트 상태 (노드들이 공유하는 데이터)
# ──────────────────────────────────────────
class AgentState(TypedDict):
    pages:          list[dict]   # 크롤링 결과
    perks:          list[dict]   # 추출된 특전
    new_perks:      list[dict]   # DB에 새로 저장된 특전
    retry_count:    int          # 재시도 횟수
    strategy_index: int          # 현재 프롬프트 전략 번호
    log:            list[str]    # 실행 로그


# ──────────────────────────────────────────
# 노드 1: 크롤링
# ──────────────────────────────────────────
def crawl_node(state: AgentState) -> AgentState:
    print("\n" + "="*60)
    print("📡 [노드 1] 크롤링 시작")
    print("="*60)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        page = context.new_page()

        for info in TARGET_URLS:
            url, chain = info["url"], info["chain"]
            crawl_type = info.get("type", "normal")
            print(f"\n  → {chain} 크롤링 중...")


            # 메가박스는 eventBtn 카드 → 상세 페이지
            if crawl_type == "megabox":
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    page.wait_for_timeout(5000)

                    # eventBtn 카드 전체 수집
                    cards = page.evaluate("""
                        () => {
                            const items = document.querySelectorAll('a.eventBtn[data-no]');
                            return Array.from(items).map(el => ({
                                eventNo: el.getAttribute('data-no'),
                                title: el.innerText.trim().split('\\n')[0]
                            }));
                        }
                    """)
                    print(f"  → 메가박스 이벤트 카드 {len(cards)}개 발견")

                    # 특전 키워드 있는 카드만 필터
                    keywords = ["증정", "굿즈", "포스터", "포토카드", "필름마크", "엽서", "스티커", "키링", "상영회"]
                    filtered = [c for c in cards if any(kw in c["title"] for kw in keywords)]
                    print(f"  → 특전 관련 {len(filtered)}개 필터링")

                    # 각 상세 페이지 텍스트 수집
                    combined = ""
                    for card in filtered[:30]:
                        detail_url = f"https://www.megabox.co.kr/event/detail?eventNo={card['eventNo']}"
                        try:
                            page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(2000)
                            text = page.evaluate("document.body.innerText")
                            combined += f"\n\n=== {card['title']} ===\n상세URL: {detail_url}\n{text[:3000]}"
                        except Exception:
                            continue

                    found = sum(1 for kw in keywords if kw in combined)
                    print(f"  ✓ {chain}: {len(combined):,}자 | 키워드 {found}개")
                    results.append({"url": url, "chain": chain, "content": combined[:100000], "keyword_count": found})
                except Exception as e:
                    print(f"  ❌ {chain} 오류: {e}")



            # 롯데시네마는 상세 페이지를 하나씩 들어가야 함
            if crawl_type == "lotte":
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    page.wait_for_timeout(5000)

                    # 이벤트 링크 목록 수집
                    links = page.evaluate("""
                        () => {
                            const anchors = document.querySelectorAll('a[href*="EventDetail"]');
                            return Array.from(anchors).slice(0, 15).map(a => ({
                                href: a.href,
                                text: a.innerText.trim()
                            }));
                        }
                    """)
                    print(f"  → 롯데시네마 이벤트 링크 {len(links)}개 발견")

                    # 각 상세 페이지 텍스트 합치기
                    combined = ""
                    for link in links[:10]:
                        try:
                            page.goto(link["href"], wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(3000)
                            text = page.evaluate("document.body.innerText")
                            combined += f"\n\n=== {link['text']} ===\n{text[:3000]}"
                        except Exception:
                            continue

                    found = sum(1 for kw in ["증정","굿즈","포스터","포토카드","필름마크","엽서"] if kw in combined)
                    print(f"  ✓ {chain}: {len(combined):,}자 | 키워드 {found}개")
                    results.append({"url": url, "chain": chain, "content": combined[:100000], "keyword_count": found})
                except Exception as e:
                    print(f"  ❌ {chain} 오류: {e}")

            # 일반 사이트
            else:
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    if resp and resp.status >= 400:
                        print(f"  ❌ HTTP {resp.status}")
                        continue
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    page.wait_for_timeout(5000)
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 1000)")
                        page.wait_for_timeout(800)

                    text  = page.evaluate("document.body.innerText")
                    found = sum(1 for kw in ["증정","굿즈","포스터","포토카드","필름마크","엽서"] if kw in text)
                    print(f"  ✓ {chain}: {len(text):,}자 | 키워드 {found}개")
                    results.append({"url": url, "chain": chain, "content": text[:100000], "keyword_count": found})
                except Exception as e:
                    print(f"  ❌ {chain} 오류: {e}")

        context.close()
        browser.close()

    log = state.get("log", [])
    log.append(f"크롤링 완료: {len(results)}개 페이지")
    print(f"\n  ✅ {len(results)}개 페이지 수집 완료")
    return {**state, "pages": results, "log": log}


# ──────────────────────────────────────────
# 노드 2: AI 분석 (특전 추출)
# ──────────────────────────────────────────
def analyze_node(state: AgentState) -> AgentState:
    strategy_index = state.get("strategy_index", 0)
    strategy       = PROMPT_STRATEGIES[strategy_index]
    pages          = state.get("pages", [])

    print("\n" + "="*60)
    print(f"🤖 [노드 2] AI 분석 - 전략: [{strategy['name']}]")
    print("="*60)

    all_perks = []
    for page in pages:
        chain, content = page["chain"], page["content"]
        print(f"\n  → {chain} 분석 중...")

        prompt = f"""{strategy['instruction']}

아래는 {chain} 이벤트 페이지 텍스트야.
각 이벤트는 "=== 제목 ===" 으로 구분되고 "상세URL: ..." 형태로 URL이 있어.

{content[:15000]}

---
반드시 JSON 배열 형식으로만 답해. 다른 설명 없이.
형식:
[
  {{
    "movie_title": "영화 제목 (꺾쇠괄호 없이, 예: 범죄도시5)",
    "benefit_type": "포스터 또는 포토카드 또는 굿즈 또는 필름마크 또는 엽서 또는 스티커 또는 키링 또는 상영회 또는 기타 중 하나",
    "week": null,
    "condition": "조건 있으면 작성, 없으면 null",
    "source_url": "해당 이벤트의 상세URL (텍스트에서 추출)",
    "detail": "특전 상세 설명"
  }}
]

특전이 없으면 [] 반환."""

        try:
            response  = llm.invoke(prompt)
            raw       = response.content.strip()
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                for item in parsed:
                    item["chain"]      = chain
                    item["source_url"] = page["url"]
                all_perks.extend(parsed)
                print(f"  ✓ {chain}: {len(parsed)}개 추출")
            else:
                print(f"  ⚠️ {chain}: JSON 파싱 실패")
        except Exception as e:
            print(f"  ❌ {chain} LLM 오류: {e}")

    log = state.get("log", [])
    log.append(f"분석 완료 [{strategy['name']}]: {len(all_perks)}개 추출")
    print(f"\n  ✅ 총 {len(all_perks)}개 특전 추출")
    return {**state, "perks": all_perks, "log": log}


# ──────────────────────────────────────────
# 노드 3: 검증 (결과가 충분한가?)
# ──────────────────────────────────────────
def validate_node(state: AgentState) -> AgentState:
    perks          = state.get("perks", [])
    retry_count    = state.get("retry_count", 0)
    strategy_index = state.get("strategy_index", 0)

    print("\n" + "="*60)
    print("🔍 [노드 3] 결과 검증")
    print("="*60)
    print(f"  추출된 특전 수: {len(perks)}개")
    print(f"  재시도 횟수: {retry_count}/3")

    log = state.get("log", [])

    if len(perks) == 0 and retry_count < 3 and strategy_index < len(PROMPT_STRATEGIES) - 1:
        print(f"  ⚠️ 특전 0개 → 다음 전략으로 재시도 결정")
        log.append(f"검증 실패: 재시도 예정 (전략 → {strategy_index + 1})")
    else:
        status = "충분한 결과" if len(perks) > 0 else "모든 전략 소진"
        print(f"  ✅ {status}")
        log.append(f"검증 완료: {len(perks)}개")

    return {
        **state,
        "retry_count":    retry_count + 1,
        "strategy_index": strategy_index + 1,
        "log":            log,
    }


# ──────────────────────────────────────────
# 조건 분기: 재시도 or 저장?
# ──────────────────────────────────────────
def should_retry(state: AgentState) -> str:
    perks          = state.get("perks", [])
    retry_count    = state.get("retry_count", 0)
    strategy_index = state.get("strategy_index", 0)

    if len(perks) == 0 and retry_count <= 3 and strategy_index < len(PROMPT_STRATEGIES):
        print(f"\n  🔄 재시도! (전략 {strategy_index}번으로)")
        return "retry"
    return "save"


# ──────────────────────────────────────────
# 노드 4: 저장
# ──────────────────────────────────────────
def save_node(state: AgentState) -> AgentState:
    perks = state.get("perks", [])

    print("\n" + "="*60)
    print("💾 [노드 4] Supabase 저장")
    print("="*60)

    if not perks:
        print("  저장할 특전 없음")
        return {**state, "new_perks": []}

    new_perks = []
    for perk in perks:
        row = {
            "chain":             perk.get("chain"),
            "movie_title":       perk.get("movie_title"),
            "week":              perk.get("week"),
            "benefit_type":      perk.get("benefit_type"),
            "condition":         perk.get("condition"),
            "source_url": perk.get("source_url") or perk.get("detail_url") or page.get("url"),
            "reliability_score": "high",
            "status":            "active",
        }
        try:
            # 같은 영화+극장+특전 조합이면 스킵
            existing = supabase.table("movie_perks")\
                .select("id")\
                .eq("chain", row["chain"])\
                .eq("movie_title", row["movie_title"])\
                .eq("benefit_type", row["benefit_type"])\
                .execute()

            if existing.data:
                print(f"  ⊗ 중복: {perk.get('movie_title')} - {perk.get('benefit_type')}")
                continue

            res = supabase.table("movie_perks").insert(row).execute()

            if res.data:
                new_perks.append(perk)
                print(f"  ✨ 신규: {perk.get('movie_title')} - {perk.get('benefit_type')}")
        except Exception as e:
            err = str(e).lower()
            if "duplicate" in err or "unique" in err:
                print(f"  ⊗ 중복: {perk.get('movie_title')}")
            else:
                print(f"  ❌ 오류: {perk.get('movie_title')}: {e}")

    log = state.get("log", [])
    log.append(f"저장 완료: 신규 {len(new_perks)}개 / 전체 {len(perks)}개")
    print(f"\n  ✅ 신규 {len(new_perks)}개 저장 완료")
    return {**state, "new_perks": new_perks, "log": log}


# ──────────────────────────────────────────
# 노드 5: Slack 알림
# ──────────────────────────────────────────
def notify_node(state: AgentState) -> AgentState:
    new_perks = state.get("new_perks", [])

    print("\n" + "="*60)
    print("📣 [노드 5] Slack 알림")
    print("="*60)

    if not SLACK_WEBHOOK_URL:
        print("  SLACK_WEBHOOK_URL 없음 → 스킵")
        return state
    if not new_perks:
        print("  신규 특전 없음 → 알림 스킵")
        return state

    lines = [f"🎬 *오늘의 신규 영화 특전 {len(new_perks)}개*\n"]
    for p in new_perks:
        week_str = f" ({p['week']}주차)" if p.get("week") else ""
        cond_str = f" | {p['condition']}" if p.get("condition") else ""
        lines.append(f"• [{p['chain']}] *{p['movie_title']}*{week_str} → {p['benefit_type']}{cond_str}")
        if p.get("source_url"):
            lines.append(f"  🔗 {p['source_url']}")

    payload = json.dumps({"text": "\n".join(lines)}).encode("utf-8")
    req     = urllib.request.Request(SLACK_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"  ✅ {len(new_perks)}개 알림 전송 완료")
    except Exception as e:
        print(f"  ❌ 전송 실패: {e}")

    log = state.get("log", [])
    log.append(f"Slack 알림: {len(new_perks)}개 전송")
    return {**state, "log": log}


# ──────────────────────────────────────────
# 그래프 조립
# ──────────────────────────────────────────
def build_graph():
    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("crawl",    crawl_node)
    graph.add_node("analyze",  analyze_node)
    graph.add_node("validate", validate_node)
    graph.add_node("save",     save_node)
    graph.add_node("notify",   notify_node)

    # 흐름 연결
    graph.set_entry_point("crawl")
    graph.add_edge("crawl",   "analyze")
    graph.add_edge("analyze", "validate")

    # 핵심: 검증 후 재시도 or 저장 분기
    graph.add_conditional_edges(
        "validate",
        should_retry,
        {
            "retry": "analyze",  # ← 0개면 analyze로 다시 돌아감!
            "save":  "save",
        }
    )

    graph.add_edge("save",   "notify")
    graph.add_edge("notify", END)

    return graph.compile()


# ──────────────────────────────────────────
# 메인
# ──────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("🤖 영화 특전 크롤러 Agent v2.0 (LangGraph)")
    print("="*60)

    agent = build_graph()

    initial_state: AgentState = {
        "pages":          [],
        "perks":          [],
        "new_perks":      [],
        "retry_count":    0,
        "strategy_index": 0,
        "log":            [],
    }

    final_state = agent.invoke(initial_state)

    # 최종 로그 출력
    print("\n" + "="*60)
    print("📋 실행 요약")
    print("="*60)
    for entry in final_state.get("log", []):
        print(f"  • {entry}")
    print("\n✅ 에이전트 완료!")


if __name__ == "__main__":
    main()