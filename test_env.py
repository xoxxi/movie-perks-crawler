import os
from dotenv import load_dotenv
from supabase import create_client

print("=" * 60)
print("🔍 환경 변수 및 Supabase 연결 테스트")
print("=" * 60)

# .env 파일 로드
load_dotenv()

# 1. 환경 변수 확인
print("\n1️⃣ 환경 변수 로드 확인:")
print("-" * 60)

openai_key = os.getenv("OPENAI_API_KEY")
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")

print(f"OPENAI_API_KEY: {'✓ 있음' if openai_key else '❌ 없음'}")
if openai_key:
    print(f"  → 길이: {len(openai_key)}자")
    print(f"  → 앞 15자: {openai_key[:15]}...")
    print(f"  → 뒤 10자: ...{openai_key[-10:]}")

print(f"\nSUPABASE_URL: {'✓ 있음' if supabase_url else '❌ 없음'}")
if supabase_url:
    print(f"  → 값: {supabase_url}")
    print(f"  → https:// 포함: {'✓' if supabase_url.startswith('https://') else '❌'}")

print(f"\nSUPABASE_ANON_KEY: {'✓ 있음' if supabase_key else '❌ 없음'}")
if supabase_key:
    print(f"  → 길이: {len(supabase_key)}자")
    print(f"  → 앞 20자: {supabase_key[:20]}...")
    print(f"  → 뒤 20자: ...{supabase_key[-20:]}")
    print(f"  → eyJ로 시작: {'✓' if supabase_key.startswith('eyJ') else '❌'}")

# 2. Supabase 연결 테스트
print("\n2️⃣ Supabase 연결 테스트:")
print("-" * 60)

if not supabase_url or not supabase_key:
    print("❌ SUPABASE_URL 또는 SUPABASE_ANON_KEY가 없습니다")
else:
    try:
        supabase = create_client(supabase_url, supabase_key)
        print("✓ Supabase 클라이언트 생성 성공")
        
        # 테스트 쿼리
        try:
            result = supabase.table("movie_perks").select("*").limit(1).execute()
            print("✓ 데이터베이스 연결 성공")
            print(f"  → 테이블 접근 가능")
            
            if result.data:
                print(f"  → 기존 레코드 수: {len(result.data)}개")
            else:
                print(f"  → 테이블이 비어있거나 레코드가 없음")
                
        except Exception as e:
            print(f"❌ 테이블 접근 실패: {e}")
            
    except Exception as e:
        print(f"❌ Supabase 클라이언트 생성 실패: {e}")

print("\n" + "=" * 60)
print("✅ 테스트 완료")
print("=" * 60)