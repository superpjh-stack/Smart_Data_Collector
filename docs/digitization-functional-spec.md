# 데이터소스 디지털화 — 기능 명세

> 상위 기획: `docs/smart-data-collector-plan.md`의 "Data Source Digitization" 절.
> 이 문서는 그 절에서 정의한 워크플로우를 구현 가능한 기능 단위로 분해한다.
> 대상 feature: `cloud-api`(태그 온보딩 API), `collector`(커미셔닝 모드 구독).

## 1. 태그 온보딩 레지스트리 (`tag_config` 확장)

**What:** 기존 `tag_config` 테이블에 온보딩 진행 상태를 추적하는 컬럼을 추가한다.

**필드 추가**

| 필드 | 타입 | 설명 |
|---|---|---|
| `source_class` | enum(`A`,`B`) | A=계측기 기존, B=계측기 신설 필요. 등록 시 필수 입력. |
| `status` | enum(`PLANNED`,`WIRING`,`COMMISSIONING`,`LIVE`) | 온보딩 진행 단계. 기본값 `PLANNED`. |
| `commissioned_at` | timestamp \| null | `status`가 `LIVE`로 전환된 시각. |
| `commissioning_started_at` | timestamp \| null | 병행 검증 시작 시각 — 최소 3일 경과 여부 판정에 사용. |
| `tolerance_pct` | float | 병행 검증 허용 오차 (기본 2%, 태그별 override 가능). |

**상태 전이 규칙 (서버측 검증)**

```
PLANNED --(배선 완료)--> WIRING --(NodeId 등록)--> COMMISSIONING --(검증 통과)--> LIVE
                                                        |
                                                        +--(검증 실패)--> WIRING (재점검)
```

- `source_class=B`인 태그는 `WIRING` 이전 단계(`PLANNED`)에서 하드웨어 설치 완료 보고 없이는
  `status` 변경 API 호출이 거부된다 (하드웨어 미완료 상태로 온보딩이 앞서가는 것을 방지).
- `LIVE`로의 전이는 아래 2번 "커미셔닝 검증" 기능이 통과 판정을 내려야만 허용 (수동으로 직접
  `LIVE`로 바꾸는 API는 audit 로그에 별도 사유 필드를 강제).

**엔드포인트**

- `POST /config/v1/tags` — 신규 태그 등록 (`status=PLANNED` 강제)
- `PATCH /config/v1/tags/{tag_id}/status` — 상태 전이 (전이 규칙 위반 시 422)
- `GET /config/v1/tags?status=COMMISSIONING` — 진행 중인 온보딩 목록 조회

---

## 2. 커미셔닝 검증 (병행 비교)

**What:** `status=COMMISSIONING`인 태그에 대해, 수집기가 읽은 OPC UA 값과 오퍼레이터가 수기로
입력한 참조값을 같은 시간대 기준으로 비교해 오차를 계산하고, 통과/실패를 판정한다.

**흐름**

1. 오퍼레이터(또는 커미셔닝 담당자)가 하루 2~3회, 현장에서 읽은 수기 값을
   `POST /config/v1/tags/{tag_id}/reference-readings` 로 입력한다 (`value`, `observed_at_utc`).
2. 서버는 `observed_at_utc` 기준 ±30초 이내의 수집기 원본 값을 조회해 오차율을 계산한다:
   `abs(auto_value - manual_value) / manual_value <= tolerance_pct`.
3. `commissioning_started_at` 이후 최소 3일간, 하루 최소 2건 이상 비교 기록이 모두 허용 오차
   이내면 자동으로 `status=LIVE` 전이 후보로 표시 (최종 승인은 사람이 확정 — 아래 3번).
4. 오차 초과 기록이 하나라도 있으면 해당 태그를 "재점검 필요" 목록에 올리고, 원인 후보
   (배선 극성, 4-20mA 스케일링, 단위 환산 오류 등)를 체크리스트로 노출한다.

**엔드포인트**

- `POST /config/v1/tags/{tag_id}/reference-readings`
- `GET /config/v1/tags/{tag_id}/commissioning-report` — 비교 기록·오차율·판정 결과 반환

---

## 3. 온보딩 승인 액션 (사람 확정)

**What:** 자동 판정은 "후보"일 뿐, 실제 `LIVE` 전환은 담당자가 커미셔닝 리포트를 확인하고
명시적으로 승인해야 한다 (완전 자동 전환 시 배선 오류가 우연히 허용 오차 안에 들어온 경우를
걸러낼 사람의 확인 절차가 없어지므로).

- `POST /config/v1/tags/{tag_id}/approve-live` — 승인자 ID, 승인 사유 필수. 이 호출 성공 시에만
  `status=LIVE`, `commissioned_at=now()` 기록.
- 승인 이력은 감사 추적을 위해 별도 로그 테이블에 남긴다 (`tag_id, approved_by, approved_at, commissioning_report_snapshot`).

---

## 4. 알람 평가 게이트

**What:** 알람 평가 엔진(collector 내부)은 `status != LIVE`인 태그를 임계치 평가 대상에서
제외한다. 값 수집·저장(TSDB 적재)은 `COMMISSIONING` 단계부터 정상 수행한다 — 병행 검증 자체가
수집된 값을 근거로 하므로 저장은 막지 않는다.

**변경 지점**

- `collector`의 알람 평가 루프가 `tag_config` pull 시 `status` 필드를 함께 받아, `status=LIVE`인
  태그만 hysteresis 평가 대상 목록에 포함시킨다.
- 대시보드 표시용으로 `COMMISSIONING` 상태 태그는 값은 보이되 "검증 중" 배지를 함께 내려준다
  (대시보드 자체 구현은 범위 밖이나, API 응답에 `status` 필드를 포함해 대시보드가 배지를 그릴 수
  있게 한다).

---

## 5. 디지털화 커버리지 API

**What:** 전체 포인트 대비 온보딩 진행률을 집계해 반환한다.

- `GET /config/v1/tags/coverage`

```json
{
  "total": 100,
  "by_status": { "PLANNED": 12, "WIRING": 8, "COMMISSIONING": 5, "LIVE": 75 },
  "by_source_class": { "A": 92, "B": 8 },
  "live_pct": 75.0
}
```

- gap analysis(PDCA Check 단계)에서 "코드 구현 완료"와 "현장 디지털화 완료"를 분리해 추적하는
  근거 지표로 사용한다 (goalmeta.md의 gap analysis 항목과 연결).

---

## Acceptance Criteria (기능 단위)

1. `source_class=B` 태그는 하드웨어 완료 보고 전 `WIRING` 이상으로 상태 전이가 불가능하다.
2. `COMMISSIONING` 상태 태그의 값은 TSDB에 적재되지만 알람은 발생하지 않는다.
3. 병행 검증 오차가 허용치를 초과한 기록이 하나라도 있으면 자동 `LIVE` 후보에서 제외된다.
4. `approve-live` 승인 없이는 어떤 경로로도 `status=LIVE`가 될 수 없다 (직접 UPDATE 금지, API 계약으로만 상태 변경).
5. `/config/v1/tags/coverage`가 실제 태그 상태 분포와 항상 일치한다 (등록·전이 직후 재조회 시 즉시 반영).

## Out of Scope (이 기능 명세 한정)

- Class B 태그의 실제 센서/배선 설치 진행 관리(공정 일정, 자재 발주 등) — 설비팀 별도 도구 영역.
- 커미셔닝 리포트의 시각화 UI 자체 — API 응답까지만 책임, 대시보드 구현은 별도 SLA.
