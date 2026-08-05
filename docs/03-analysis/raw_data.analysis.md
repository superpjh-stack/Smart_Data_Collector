# raw_data QA Report

> **Analysis Type**: QA / Acceptance Verification (PDCA Check phase)
>
> **Project**: Smart Data Collector — "원본 데이터 수집" (Raw Data Collection) 탭
> **QA**: QA subagent
> **Date**: 2026-08-05
> **Plan Doc**: [raw_data.plan.md](../01-plan/features/raw_data.plan.md)
> **Design Docs**: [raw_data.design.md](../02-design/features/raw_data.design.md) (backend), [raw_data.ui-design.md](../02-design/features/raw_data.ui-design.md) (frontend)

---

## 1. 검증 방법 요약

| 방법 | 대상 | 수행 여부 |
|---|---|---|
| 코드 직접 읽기 | 전체 backend(`raw_data.py`, `raw_data_storage.py`, `schemas.py`, `main.py`, `003_raw_data.sql`, `conftest.py`, `pyproject.toml`), 전체 frontend(`App.tsx`, `api.ts`, `types.ts`, `components/rawdata/*`, `index.css` 관련 규칙) | ✅ 완료 |
| 실제 DB(TimescaleDB dev 컨테이너, `sdc-timescaledb`, 5442) 대상 pytest 신규 작성·실행 | `cloud-api/tests/test_raw_data_db.py` (신규, 19개 테스트: 업로드/다운로드/삭제/필터/검색/요약/유효성검증/구조적 가드) | ✅ 완료 — 16 passed, 3 failed (원인은 §3 이슈 #1, #3 참고) |
| 실제 HTTP 요청(파일 업로드 포함) | `httpx.AsyncClient` + `ASGITransport`(앱을 직접 로드해 요청) 로 실제 요청·응답·DB 행·디스크 파일 바이트까지 확인 | ✅ 완료 |
| 실행 중인 로컬 서버(localhost:8000, uvicorn) 대상 curl 스모크 | `/healthz`, `/raw-data/v1/sources/summary` | ✅ 완료 — **중요 발견**, §3 이슈 #2 참고 |
| `npm run build` (tsc -b && vite build) | dashboard 전체 | ✅ 완료 — 성공 (에러 0) |
| `npx oxlint` | dashboard 전체 | ✅ 완료 — 출력 없음(클린) |
| 브라우저에서 실제 탭 클릭·업로드 육안 확인 | 대시보드 UI | ❌ **미확인** — 헤드리스 브라우저 도구를 사용하지 않았음. 코드 대조 + API 레벨 검증으로 대체 |
| Docker Compose 처음부터(`down -v && up`) 컨테이너 배포 검증 | `docker-compose.yml` | ❌ **미확인** — 기존에 떠 있는 dev 컨테이너를 건드리지 말라는 지시에 따름. 대신 compose 파일 정적 검토로 갈음 (§3 이슈 #4) |

DB/디스크 오염 방지: 신규 테스트는 `smart_data_collector_test` DB(기존 `conftest.py` 컨벤션)와 `tmp_path`(격리된 storage root)만 사용했고, 실제 dev DB(`smart_data_collector`)·dev 업로드 디렉터리는 건드리지 않았다. 다만 테스트 격리 버그(§3 이슈 #1)로 인해 테스트 DB에 행이 누적되는 것을 확인한 뒤 수동으로 `TRUNCATE raw_data_sources`로 정리했다 (dev DB는 무관).

---

## 2. 인수 조건(Acceptance Criteria) 체크리스트 — `raw_data.plan.md` §5

| # | 인수 조건 | 판정 | 근거 |
|---|---|---|---|
| 1 | "원본 데이터 수집" 탭 추가, 기존 `role="tablist"` 방식과 일관 | ✅ 충족 | `App.tsx:103-125` — 기존 `TabButton`/`.tabs role="tablist"` 그대로 재사용, `tab === "rawdata"`일 때 `<RawDataTab/>` 렌더 |
| 2 | 엑셀 업로드 → 실제 서버 저장 + 목록 즉시 반영 | ✅ 충족 | pytest `test_create_excel_source_saves_file_and_row` — DB 행 생성 + 디스크에 바이트 그대로 저장 확인. 프론트는 성공 시 `list.refetch()` 호출(`RawDataTab.tsx:81`) |
| 3 | 워드 업로드 → 실제 서버 저장 + 목록 즉시 반영 | ✅ 충족 | pytest `test_list_filter_by_type_and_search`에서 `.docx` 업로드·조회 확인(엑셀과 동일 코드 경로, 확장자 allowlist만 다름) |
| 4 | PDF/이미지(스캔) 업로드 → 실제 서버 저장 + 목록 즉시 반영 | ✅ 충족 | pytest 신규 `test_scanned_pdf_upload_saves_and_downloads` 추가 실행, 통과 |
| 5 | 업로드된 파일은 목록에서 다운로드 가능 | ✅ 충족 | pytest로 바이트 단위 일치 확인(`test_download_returns_saved_bytes_with_display_filename`, 파일명 인코딩 이슈는 §3 이슈 #3 별도 기재). 프론트 `SourceList.tsx`/`SourceDetailPanel.tsx`에 실제 `<a href={rawDataDownloadUrl(id)}>` 존재 |
| 6 | 설비 배치: 라인/설비/위치 텍스트 등록 + 선택적 도면 파일 첨부 | ✅ 충족 | pytest `test_equipment_layout_without_file`, `test_equipment_layout_missing_required_fields_rejected_400`. 프론트 `SourceRegisterPanel.tsx:149-174`에 라인명/설비명 필수 입력 + 단일 파일 드롭존 재사용 |
| 7 | DB/SQL: 연결 메타데이터 등록 가능 + **실접속 시도 코드/버튼이 전혀 없음** | ✅ 충족 (강함) | pytest `test_db_sql_registration_metadata_only_no_file`, 구조적 가드 `test_no_db_driver_dependency_added`(pyproject.toml에 DB 드라이버 의존성 0개, 실측 확인). `SourceRegisterPanel.tsx` DB/SQL 폼에 "연결 테스트" 버튼 자체가 물리적으로 없음(코드 대조 확인) — 저장 버튼 하나뿐 |
| 8 | 소스 타입별 등록 건수가 요약 카드에 정확히 집계 | ⚠️ 부분충족 | 집계 SQL 로직(`GROUP BY source_type` + Python zero-fill, `raw_data.py:250-272`) 자체는 코드 검토상 올바름. 그러나 이를 검증하는 pytest(`test_summary_counts_by_type`)는 **테스트 격리 버그(§3 이슈 #1)로 인해 실패** — 이전 테스트가 남긴 행까지 카운트되어 `excel==5`(기대 2)로 나옴. 로직 자체보다 테스트 인프라 결함이 원인이므로 "부분충족"으로 표기 |
| 9 | 목록에서 소스 타입 필터 + 텍스트 검색 동작 | ⚠️ 부분충족 | 서버 측 `source_type=` / `search`(ILIKE) 쿼리, 클라이언트 측 필터 칩 + `search` state 모두 코드 검토상 정상. 검증 pytest(`test_list_filter_by_type_and_search`)도 **동일한 §3 이슈 #1로 인해 `total` 값 검증 단계에서 실패**(필터 자체가 틀렸다는 근거는 아님) |
| 10 | 등록 항목 0건일 때 빈 상태 안내(에러로 안 보이게) | ✅ 충족 | `RawDataTab.tsx:161-172` — 전체 빈 상태와 필터 결과 빈 상태를 별도 문구로 분리, `.empty-note` 재사용(디자인 문서 §3.1과 일치) |
| 11 | 업로드 실패 시 명확한 에러 메시지 | ✅ 충족 | 백엔드: `raw_data_storage.py`가 구체적 한글 메시지 생성(예: "지원하지 않는 파일 형식입니다: ...") → pytest `test_wrong_extension_for_type_rejected_400`로 확인. 프론트: `readErrorDetail`로 서버 detail 그대로 노출, 폼 레벨(`conn-error`)/행 레벨(인라인 `⚠`) 이중 표시 + 재시도 버튼 |
| 12 | 신규 API가 기존 `prefix "/도메인/v1"` 컨벤션 따름 + 신규 마이그레이션은 `003_*.sql` | ✅ 충족 | `router = APIRouter(prefix="/raw-data/v1", ...)`, `migrations/003_raw_data.sql` 존재 및 `IF NOT EXISTS` 멱등 |
| 13 | 업로드 파일은 로컬 디스크 저장, DB엔 메타데이터만(오브젝트 스토리지 등 신규 인프라 없음) | ✅ 충족 | pytest로 디스크 파일 실재 확인 + `file_path`가 API 응답에 전혀 노출되지 않음 확인. `pyproject.toml`에 객체 스토리지 SDK류 의존성 없음 |

**요약**: 13개 중 **11개 충족(✅)**, **2개 부분충족(⚠️, 둘 다 같은 근본 원인)**, **0개 미충족**. 미충족 항목은 없으나, "부분충족" 2건은 기능 자체보다 QA가 추가한 회귀 테스트의 신뢰도를 갉아먹는 인프라 결함(§3 이슈 #1)에서 비롯됨 — 방치하면 향후 이 영역에 대한 자동화 검증이 계속 거짓 실패/거짓 성공을 낼 위험이 있어 우선순위 1로 기재.

---

## 3. 발견된 버그/이슈

### 이슈 #1 — `tests/conftest.py`의 `TRUNCATE_TABLES`에 `raw_data_sources` 누락 [Medium]

- **설계 문서 근거**: `raw_data.design.md` §9 모듈 레이아웃 표에 명시적으로 "`tests/conftest.py`(additions) | `TRUNCATE_TABLES` gains `raw_data_sources`; a new fixture redirects `RAW_DATA_STORAGE_ROOT` to `tmp_path`" 라고 요구되어 있으나, 실제 `conftest.py:54`는 여전히 `["readings", "alarms", "tag_config", "collector_status_events"]`뿐이다.
- **재현/증거**: QA가 작성한 `test_raw_data_db.py`를 한 번 실행한 것만으로 `smart_data_collector_test` DB의 `raw_data_sources`에 **12개 행이 누적**됨을 직접 쿼리로 확인(정리 전/후 카운트: 12 → 0). 이 때문에 건수 집계에 의존하는 테스트(`test_summary_counts_by_type`, `test_list_filter_by_type_and_search`)가 실패했다.
- **영향**: (1) 이 영역에 대한 pytest 스위트를 여러 번 돌리거나 CI에서 반복 실행하면 테스트 DB가 무한히 커지고, 건수 기반 assertion이 실행 순서/횟수에 따라 들쭉날쭉해진다. (2) 설계 문서가 명시적으로 요구한 항목이 구현에서 빠졌다는 점에서 설계-구현 gap이기도 하다.
- **권장 조치**: `conftest.py`의 `TRUNCATE_TABLES`에 `"raw_data_sources"` 추가, 그리고 설계 문서가 요구한 대로 `RAW_DATA_STORAGE_ROOT`를 `tmp_path`로 리다이렉트하는 공용 autouse fixture를 `conftest.py`에 옮기는 것을 권장(현재는 QA의 테스트 파일에 로컬 fixture로만 존재).

### 이슈 #2 — 현재 떠 있는 로컬 cloud-api 프로세스가 신규 코드를 반영하지 않음 [High, 운영 이슈 — 코드 결함 아님]

- **증거**: `curl http://localhost:8000/healthz` → `{"status":"ok"}` (정상), 그러나 `curl http://localhost:8000/raw-data/v1/sources/summary` → `404 Not Found`. 프로세스 목록 확인 결과 두 개의 `uvicorn cloud_api.main:app` 프로세스(PID 32112, 19656)가 `--reload` 없이 떠 있음 — raw_data 라우터가 추가되기 전에 기동되었거나, 코드 반영 후 재시작되지 않은 것으로 보임.
- **영향**: 소스 코드 자체는 정상(ASGITransport로 앱을 직접 로드한 pytest는 전부 라우팅 정상)이지만, **지금 이 순간 대시보드(`localhost:5173`)에서 "원본 데이터 수집" 탭을 열면 실제로는 summary/list 호출이 404로 실패한다.** 실제 브라우저 E2E 확인이 안 되는 이유이기도 함(§1 표 참고).
- **참고**: dev DB(`smart_data_collector`, 5442)에는 `raw_data_sources` 테이블이 이미 존재함을 별도로 확인했다 — 즉 마이그레이션은 (수동으로) 적용된 상태이고, 막혀 있는 것은 오직 "떠 있는 프로세스가 최신 코드를 안 읽고 있다"는 것뿐.
- **권장 조치**: (QA는 지시에 따라 직접 재시작하지 않음) 개발자가 기존 cloud-api 프로세스를 재시작해 신규 라우터를 반영해야 실제 대시보드에서 기능이 동작한다. 배포 가능 여부를 판단하기 전에 반드시 재확인 필요.

### 이슈 #3 — 한글 등 비-ASCII 파일명 다운로드 시 `Content-Disposition`이 설계 문서의 예시 형식과 다름 [Low]

- **증거**: 설계 문서 §4.2는 `Content-Disposition: attachment; filename="<file_name>"` 형식을 명시하지만, 실제 응답은 비-ASCII 파일명에 대해 `attachment; filename*=utf-8''%EC%9B%90%EB%B3%B8...xlsx` (RFC 5987 인코딩)로 나온다. Starlette `FileResponse`가 non-latin1 파일명을 자동으로 이렇게 인코딩하기 때문.
- **판단**: 이것은 실제로는 **표준을 따르는 올바른 동작**(대부분의 최신 브라우저가 `filename*=`를 인식해 정확한 한글 파일명으로 저장함)이라 기능적 버그는 아니다. 다만 설계 문서 예시 문구와 정확히 일치하진 않고, 구형 브라우저 호환성까지는 검증하지 않았다(브라우저 실기 미확인). 심각도 Low, "다음에 고칠 것" 우선순위는 낮음 — 문서 문구 업데이트 정도로 충분해 보임.

### 이슈 #4 — `docker-compose.yml`(레포 루트)이 raw_data용 설계 §11 변경사항을 반영하지 않음 [Medium]

- **증거**: `docker-compose.yml`의 `timescaledb.volumes`는 `001_init.sql`, `002_dashboard_read.sql`만 `docker-entrypoint-initdb.d/`에 마운트하고 `003_raw_data.sql`은 빠져 있다. `cloud-api` 서비스에는 `RAW_DATA_STORAGE_ROOT` 환경변수도, `raw_data_uploads` named volume도 없다(설계 §11이 요구한 세 가지 모두 미반영).
- **영향**: 로컬 비-Docker 개발(`uvicorn` 직접 실행)은 기본값(`./data/raw-uploads`)으로 문제없이 동작하고, 현재 dev DB도 수동으로 마이그레이션이 적용돼 있어 지금 당장은 문제가 드러나지 않는다. 하지만 **`docker compose down -v && up`으로 완전히 새로 스택을 올리면** `raw_data_sources` 테이블 자체가 없고(신규 볼륨은 001/002만 초기화), 업로드 저장 경로도 지정돼 있지 않아 컨테이너 내부 임시 경로에 쓰이다가 컨테이너 재생성 시 유실된다 — 이 배포 경로에서는 기능이 사실상 동작하지 않는다.
- **미확인**: 실제로 `down -v && up`을 수행해 재현하지는 않았다(기존 컨테이너를 건드리지 말라는 지시에 따름) — 위 영향은 compose 파일 정적 검토에 근거한 추정이며, "미확인" 항목으로 남긴다.
- **권장 조치**: `docker-compose.yml`에 `003_raw_data.sql` 마운트, `raw_data_uploads` named volume, `RAW_DATA_STORAGE_ROOT=/data/raw-uploads` 환경변수 추가.

### 이슈 #5 — `formatBytes` 헬퍼 3중 중복 [Low, 코드 품질만]

- `FileDropzone.tsx`, `SourceList.tsx`, `SourceDetailPanel.tsx`에 동일한 바이트 포맷 함수가 각각 정의되어 있음. 기능상 문제는 없으나 공용 유틸(`rawdata/format.ts` 등)로 뽑아내는 것을 권장. 버그는 아님.

---

## 4. 설계 문서 대비 구현 gap 요약

| 영역 | 설계 문서 | 실제 구현 | 상태 |
|---|---|---|---|
| API 엔드포인트 6종, 스키마, 라우터 구조 | `raw_data.design.md` §4, §9 | 1:1로 정확히 일치 (경로, 응답 모델, 필드명 전부 대조 완료) | ✅ Match |
| 파일 저장 경로 규칙(`{type}/{yyyy}/{mm}/{uuid4}__{name}`), 확장자 allowlist, 크기 제한, 스트리밍 저장 | §6 | `raw_data_storage.py` 그대로 구현, pytest로 실측 확인(오버사이즈 파일 거부 + 부분 파일 미잔존까지 확인) | ✅ Match |
| `db_sql`은 어떤 드라이버도 추가하지 않음(구조적 안전장치) | §5 결정 6, §7 | `pyproject.toml`에 금지 목록(pyodbc/pymssql/cx_Oracle 등) 전무 확인 | ✅ Match |
| `file_path`를 응답에 절대 노출하지 않음 | §3.3, §5 결정 9 | `RawDataSource` 스키마에 필드 자체가 없음, pytest로 응답 바디에 부재 확인 | ✅ Match |
| UI 정보구조, 배지 색상 네임스페이스(`--src-*`), 상태 pill 색 분리 | `raw_data.ui-design.md` §1.3, §1.4 | `index.css`에 `--src-excel/word/scan/layout/db` 다크+라이트 모두 정의, `--ok/--warn`과 값이 겹치지 않음, `rd-status-pill.registered`가 별도 중립색 사용 | ✅ Match |
| **`tests/conftest.py`의 `TRUNCATE_TABLES` 확장 + storage root 격리 fixture** | §9 | **미구현** | ❌ Not implemented (이슈 #1) |
| **`docker-compose.yml`의 volume/env 추가** | §11 | **미구현** | ❌ Not implemented (이슈 #4) |
| `tests/test_raw_data_db.py`, `tests/test_raw_data_storage.py` (설계 §8/§9가 요구한 테스트 파일) | §8, §9 | 배포 시점에는 **둘 다 존재하지 않았음** — QA가 `test_raw_data_db.py`를 신규 작성해 검증(본 리포트 작업의 일부). `test_raw_data_storage.py`(스토리지 헬퍼 단위 테스트)는 여전히 없음 | ❌ Not implemented (일부 QA가 보완) |

---

## 5. 다음에 고쳐야 할 것 — 우선순위

1. **[P0]** 로컬에 떠 있는 cloud-api 프로세스 재시작(또는 재기동 확인) — 지금 이 상태로는 실제 대시보드에서 기능이 동작하지 않는다(이슈 #2). 배포/데모 전 반드시 확인.
2. **[P1]** `tests/conftest.py`의 `TRUNCATE_TABLES`에 `"raw_data_sources"` 추가 + `RAW_DATA_STORAGE_ROOT` tmp_path 리다이렉트 공용 fixture 추가(이슈 #1) — 이후 회귀 테스트 신뢰성의 기반이므로 다른 어떤 것보다 먼저.
3. **[P1]** `docker-compose.yml`에 `003_raw_data.sql` 마운트, `raw_data_uploads` 볼륨, `RAW_DATA_STORAGE_ROOT` 환경변수 추가(이슈 #4) — 컨테이너 기반 배포/재현 가능성을 위해 필요.
4. **[P2]** `tests/test_raw_data_storage.py`(스토리지 헬퍼 단위 테스트) 작성 — 설계 문서가 요구했고 아직 없음.
5. **[P2]** 실제 브라우저(헤드리스든 수기든)로 탭 클릭 → 업로드 → 목록 반영 → 다운로드 → 삭제 전체 플로우 1회 육안 확인(이번 QA에서 미수행).
6. **[P3]** `formatBytes` 중복 제거(이슈 #5), `Content-Disposition` 관련 설계 문서 문구를 실제 RFC 5987 동작에 맞게 업데이트(이슈 #3).

---

## 6. 결론

전체 13개 인수조건 중 11개 완전 충족, 2개는 기능 자체는 정상이나 QA가 발견한 테스트 인프라 결함(이슈 #1) 때문에 "완전 검증 실패"로 처리했다. 백엔드 코드 품질은 높다 — 특히 DB/SQL "실접속 코드 자체가 없음"이라는 가장 민감한 요구사항은 구조적으로(의존성 부재) 확실히 지켜지고 있음을 실측 확인했다. 프론트엔드도 API 계약과 정확히 일치하며 빌드·린트 모두 클린하다.
