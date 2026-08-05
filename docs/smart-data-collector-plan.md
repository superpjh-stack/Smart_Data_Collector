# Smart Data Collector — 기획 문서

## Context

현재 PLC 데이터는 수기로 기록되고 있어 실시간성이 없고, 설비 예지보전(predictive maintenance)에
활용할 수 없다. OPC UA 게이트웨이를 통해 PLC 10대(태그 각 10개, 총 100 포인트)에서 5초 주기로
데이터를 수집하여, 대시보드까지 5초 이내 지연으로 반영하는 Smart Data Collector가 필요하다.

**이해관계자:**
- 현장 오퍼레이터 — 대시보드에서 실시간 설비 상태/알람 확인
- MES/ERP 및 예지보전 시스템 — 정형화된 데이터로 이상 패턴 분석
- 데이터 엔지니어 — 장기 축적 데이터로 모델 학습/분석

## Current State

- PLC → OPC UA 게이트웨이는 이미 존재 (OT망)
- 수집기/저장소/대시보드는 없음 (그린필드)
- 데이터는 수기로 기록됨 → 이번 프로젝트로 완전 디지털화

## Scope

**In scope**
- OPC UA 게이트웨이로부터 읽기 전용(read-only) 데이터 수집
- 5초 주기 수집, 대시보드 반영까지 5초 이내 지연
- 태그별 임계치(threshold) 기반 알람 평가
- 네트워크/저장소 장애 시 로컬 버퍼링 후 복구 재전송
- Raw 데이터 1년 보관 후 압축 아카이브

**Out of scope**
- PLC로의 쓰기(write-back) — 향후 별도 이슈
- 고급 예지보전 ML 모델(이상 탐지 모델 자체) — 본 프로젝트는 데이터 파이프라인까지만
- 다중 사이트/공장 통합 대시보드 — 1차는 단일 사이트

## Network / Deployment Topology

```
[PLC x10] --(OPC UA)--> [OPC UA Gateway] --(OT망, 로컬)--> [Edge Collector]
                                                                 |
                                                    (아웃바운드 전용, TLS, 방화벽/DMZ)
                                                                 v
                                                        [Cloud Ingestion API]
                                                                 |
                                     +---------------------------+---------------------------+
                                     v                                                       v
                          [Hot Store: Time-series DB]                          [Alarm Event Store]
                          (raw, 1년 보관)                                        (임계치 이벤트)
                                     |
                          (1년 경과 후 배치 압축)
                                     v
                          [Cold Store: Object Storage, Parquet]
                                     |
                                     v
                    [대시보드] <---- [MES/ERP 연동 API] <---- [분석/BI]
```

- **Edge Collector**는 OT망에 위치하되 **아웃바운드 연결만** 클라우드로 개방 (인바운드 차단).
- OPC UA 클라이언트는 **Sign & Encrypt** 보안 모드 사용.
- 클라우드 수신 구간은 TLS(상호 인증 권장)로 암호화.

## Data Model

### 1. 시계열 포인트 (Tag Reading)

```json
{
  "plc_id": "PLC-01",
  "tag_id": "TEMP_01",
  "tag_name": "Bearing Temperature",
  "timestamp_utc": "2026-07-13T09:00:05.120Z",
  "value": 68.4,
  "data_type": "float",
  "unit": "celsius",
  "quality": "Good",          // OPC UA StatusCode 매핑: Good | Uncertain | Bad
  "seq": 184213                // 수집기 로컬 시퀀스 번호 (중복/유실 감지용)
}
```

- `quality`는 OPC UA StatusCode를 3단계로 정규화 (Good/Uncertain/Bad) — 다운스트림에서 불량 데이터 필터링에 사용.
- `seq`는 로컬 버퍼 기준 단조 증가 번호. 재전송 시 idempotency key로 사용 (`plc_id + tag_id + seq` 조합이 유니크).

### 2. 알람 이벤트 (Threshold Alarm)

```json
{
  "alarm_id": "PLC-01:TEMP_01:20260713T090010.000Z:184213",  // 결정적 key: plc_id:tag_id:triggered_at_utc:seq
  "plc_id": "PLC-01",
  "tag_id": "TEMP_01",
  "severity": "HIGH",          // LOW | MEDIUM | HIGH | CRITICAL
  "condition": "value > 80",
  "triggered_value": 82.1,
  "triggered_at_utc": "2026-07-13T09:00:10.000Z",
  "cleared_at_utc": null,
  "ack_status": "UNACKED",     // UNACKED | ACKED | AUTO_CLEARED
  "config_version": 42          // 알람 트리거 당시 적용 중이던 tag_config 버전 (사후 감사 추적용)
}
```

- `alarm_id`는 랜덤 UUID가 아니라 **`plc_id:tag_id:triggered_at_utc:seq` 결정적 조합**으로 생성한다.
  클라우드 수신 API는 `(plc_id, tag_id, triggered_at_utc, seq)`에 유니크 제약을 걸어, 엣지 수집기가
  크래시 후 같은 알람을 재평가해도 서버가 중복 삽입을 자동 거부한다.
- 임계치는 태그별 설정 테이블(`tag_config`)에서 관리: `tag_id, min_alarm, max_alarm, clear_margin, deadband, severity`.
- **Hysteresis(트리거/해제 임계치 분리):** `max_alarm=80, clear_margin=2`이면 80 이상에서 트리거되고
  78 이하로 내려가야 해제된다. 단순 임계치 하나만 쓰면 값이 경계 근처에서 진동할 때 알람이
  반복 생성/해제되는 "알람 플래핑"이 발생해 오퍼레이터가 알람을 무시하게 되므로, hysteresis로
  방지한다.
- 알람 평가는 **엣지 수집기에서** 수행 (클라우드 왕복 지연 없이 즉시 감지).

### 3. 태그 설정 (Tag Configuration) — 정적 메타데이터

| 컬럼 | 설명 |
|---|---|
| `plc_id` | PLC 식별자 |
| `tag_id` | OPC UA NodeId에 매핑되는 논리 태그 ID |
| `opc_node_id` | 실제 OPC UA NodeId (예: `ns=2;s=Line1.PLC01.Temp`) |
| `unit`, `data_type` | 단위, 자료형 |
| `min_alarm`, `max_alarm`, `clear_margin`, `deadband` | 임계치, 해제 히스테리시스, 노이즈 억제 범위 |
| `sampling_interval_ms` | 기본 5000, 태그별 override 가능 |
| `config_version` | tag_config 변경 시 증가하는 버전 번호 (엣지 수집기 pull 동기화에 사용) |
| `source_class` | `A`(계측 기 존재, OPC UA 노출만 필요) \| `B`(계측기 자체 신설 필요) — [[디지털화 대상 분류]] 참조 |
| `status` | `PLANNED \| WIRING \| COMMISSIONING \| LIVE` — 태그 온보딩 진행 단계, [[태그 온보딩 워크플로우]] 참조 |
| `commissioned_at` | `status`가 `LIVE`로 전환된 시각 (병행검증 통과 시점) |

### 4. tag_config 동기화 (클라우드 → 엣지)

- 엣지 수집기는 **주기적으로(예: 60초마다) `GET /config/v1/tags?since_version=N`을 폴링**해 변경분만
  받아온다. 재시작 시에만 반영되는 방식은 임계치 변경이 즉시 현장에 반영되지 않는 문제가 있어 채택하지 않았다.
- 수집기는 현재 적용 중인 `config_version`을 상태 정보로 노출해, 대시보드에서 "임계치 변경이
  반영되었는지"를 확인할 수 있다.
- `status != LIVE`인 태그는 구독은 하되(값 수집·기록은 계속), **알람 평가에서는 제외**한다 —
  아래 "데이터 소스 디지털화" 절 참조.

## Data Source Digitization (데이터소스 디지털화 상세)

"Current State"에 "데이터는 수기로 기록됨 → 이번 프로젝트로 완전 디지털화"라고만 되어 있어, 100개
포인트 전부가 동일한 방식으로 디지털화된다고 오인하기 쉽다. 실제로는 포인트마다 물리적 준비 상태가
다르므로, 온보딩 전에 반드시 다음 두 클래스로 구분한다.

### 디지털화 대상 분류

| 분류 | 현재 상태 | 필요한 작업 | 소요 |
|---|---|---|---|
| **Class A** | 센서가 이미 PLC에 연결되어 PLC 내부 레지스터에 값이 존재하지만, 오퍼레이터가 PLC 화면을 보고 종이 대장에 옮겨 적는 중 | OPC UA 게이트웨이에서 해당 레지스터를 NodeId로 노출 → `tag_config` 등록 → 커미셔닝 검증. **하드웨어 변경 없음** | 소프트웨어 온보딩만, 태그당 반나절~1일 |
| **Class B** | 계측기 자체가 없어 오퍼레이터가 휴대용 측정기(온도계 등)로 직접 재서 기록 | 센서/변환기 설치 + PLC I/O 모듈 배선 + (그 다음) Class A와 동일한 온보딩 | **하드웨어 투자 필요 — 본 프로젝트 범위 밖**, 설비팀/전기팀 별도 작업으로 분리 |

- 착수 전 100개 포인트 전수 조사로 Class A/B 비율을 확정한다. Class B 포인트는 `tag_config`에
  `status=PLANNED`으로만 등록해 두고, 하드웨어 설치가 완료되기 전까지 수집 파이프라인에 포함하지 않는다.
- Acceptance Criteria의 "PLC 10대 × 태그 10개(총 100포인트)" 수집률은 **Class A로 확인된 포인트
  기준**으로 우선 검증하고, Class B는 하드웨어 완료 후 동일 기준으로 순차 편입한다.

### 태그 온보딩 워크플로우 (Class A 기준)

1. **식별**: PLC I/O 리스트/도면에서 해당 계측 포인트의 PLC 레지스터 주소 확인.
2. **노출**: 기존 OPC UA 게이트웨이 설정에서 해당 레지스터를 NodeId(`ns=2;s=Line1.PLC01.Temp` 형식)로
   노출하도록 게이트웨이 엔지니어에게 요청 (게이트웨이 자체는 재구축하지 않음, "What Already Exists" 참조).
3. **등록**: `tag_config`에 `plc_id, tag_id, opc_node_id, unit, data_type, min_alarm/max_alarm/clear_margin/deadband, source_class=A, status=WIRING` 입력.
4. **구독 개시, 상태 = COMMISSIONING**: 수집기가 해당 NodeId를 구독해 값을 수집·저장하기 시작하지만,
   `status=COMMISSIONING`인 동안은 **알람 평가 대상에서 제외**하고 대시보드에는 "검증 중" 배지로 표시한다
   — 아직 검증되지 않은 값으로 오탐 알람이 발생해 오퍼레이터 신뢰를 잃는 것을 방지한다.
5. **병행 검증(최소 3일 권장)**: 기존 수기 기록과 수집기가 읽은 OPC UA 값을 동일 시각 기준으로
   나란히 비교한다. 오차가 태그별 허용 범위(예: ±2%) 이내로 일정 기간 유지되면 통과.
6. **전환**: 검증 통과 시 `status=LIVE`, `commissioned_at` 기록. 이 시점부터 알람 평가 대상에
   포함되고, 대시보드에서 "검증 중" 배지가 사라진다. 이후에도 수기 기록은 즉시 중단하지 않고
   "Rollback Plan"에 정의된 병행 운영 기간을 따른다 (수집기가 기존 프로세스를 대체하지 않고 병행).
7. **실패 시**: 오차가 허용 범위를 벗어나면 `status`를 `WIRING`으로 되돌리고 배선/스케일링(단위 변환,
   4-20mA ↔ 엔지니어링 단위 매핑 등) 재점검 후 5번부터 재시도.

### 디지털화 커버리지 추적

- `GET /config/v1/tags/coverage` — `status`별 태그 개수를 집계해 반환 (`PLANNED/WIRING/COMMISSIONING/LIVE`
  개수와 전체 대비 비율). 대시보드에 "100개 중 N개 LIVE (X%)" 형태의 진행률 타일로 노출한다.
- 이 지표는 "완전 디지털화"라는 목표를 하나의 통과/실패가 아니라 **진행률로 추적 가능한 값**으로
  만들어, PDCA Check 단계의 gap analysis에서 "수집기 코드가 완성됐는가"와 "현장 포인트가 실제로
  몇 % 디지털화됐는가"를 별도 지표로 구분해 볼 수 있게 한다.

## Collection Strategy (OPC UA)

- **Polling이 아닌 Subscription 기반 수집** 권장: OPC UA `CreateMonitoredItems`로 각 태그를 등록,
  `SamplingInterval=5000ms`, `PublishingInterval=5000ms`로 설정. 값이 deadband 이내로 변하지 않으면
  전송을 생략해 네트워크/저장 비용을 줄인다 (예지보전 특성상 급변 감지가 중요하므로 deadband는 작게).
- 게이트웨이 재연결 시 자동 재구독(subscription) 로직 필요.

## Local Buffering & Failure Recovery

- 엣지 수집기 로컬에 **SQLite 기반 append-only 버퍼** 사용 (경량, 임베디드, 정전에도 WAL로 안전).
- 클라우드 전송 성공 시에만 버퍼에서 커밋 오프셋 이동 (at-least-once 전송, 클라우드 측에서 `seq` 기준 dedup).
- 전송 실패 시 지수 백오프(exponential backoff) 재시도, 로컬 버퍼는 최소 72시간치 용량 확보 (100 포인트 × 5초 주기 기준 계산 시 여유 있음).
- 버퍼 디스크 용량 임계치 도달 시 알람 발생 (자체 모니터링).
- **우선순위 큐:** 버퍼는 단일 FIFO가 아니라 `type='alarm'` 레코드를 항상 `type='reading'`보다 먼저
  전송한다 (`SELECT ... WHERE type='alarm' ORDER BY seq` 를 readings 전송보다 먼저 소진). 재연결
  직후 밀린 readings(분당 최대 1,200건)가 쌓여 있어도, 그 사이 발생한 알람이 readings 뒤에서
  줄서지 않고 즉시 전송되도록 보장한다.
- **게이트웨이 장애 감지:** 수집기는 OPC UA 구독 연결이 N초 이상 끊기면 이를 `collector_status=gateway_down`
  자가진단 이벤트로 생성해 별도 채널로 즉시 전송한다 (일반 readings/alarms 큐와 무관하게 최우선 처리).
  대시보드는 이 이벤트로 "설비 정상"과 "데이터 수집 불가"를 구분해 표시한다 — 구분 없이는 게이트웨이
  장애가 "설비 정상"으로 오인될 위험이 있다.

## Transmission Format

- 배치 전송: 5~10초 단위로 모아 **JSON Lines + gzip**으로 HTTPS POST (데이터량이 초당 20포인트 수준으로 적어 Protobuf 등 바이너리 최적화는 과설계).
- 엔드포인트: `POST /ingest/v1/readings` (batch), `POST /ingest/v1/alarms` (batch)
- 각 요청에 `idempotency_key` 헤더 (배치 단위 UUID) 포함 — 재전송 시 서버가 중복 처리 방지.
- **중복 배치 응답 계약:** 서버는 중복을 에러(409)로 다루지 않는다. at-least-once 전송에서 중복은
  정상적으로 발생하는 상황이므로, 항상 `200 OK`와 `{"inserted": N, "duplicates": M}` 형태로 응답한다.
  수집기는 200 응답을 받으면 (중복 포함) 버퍼 오프셋을 커밋한다. 이렇게 하면 클라이언트 재시도 로직에
  에러 분기 처리가 필요 없다.

## Storage & Retention

| 계층 | 용도 | 보관 기간 | 포맷 |
|---|---|---|---|
| Hot (Time-series DB, 예: TimescaleDB/InfluxDB) | 대시보드 실시간 조회, 최근 분석 | 1년 (raw) | 네이티브 TSDB 포맷 |
| Cold (Object Storage, 예: S3/Blob) | 장기 보관, BI/ML 학습용 | 1년 이후 무기한 | Parquet (일/월 단위 파티션) |

- **1년 경과 데이터 이전 절차 (copy-then-verify-then-delete):**
  1. 해당 기간 데이터를 Parquet로 쓴다.
  2. 원본(Hot store)과 Parquet의 행 수를 일치 검증한다(`SELECT COUNT(*)` 비교, 파티션 단위).
  3. 일치가 확인된 파티션만 Hot store에서 삭제한다.
  검증 실패 시 삭제를 건너뛰고 재시도/알람을 발생시킨다 — write와 delete 사이에 검증 없이 곧바로
  넘어가면 부분 실패 시 데이터가 영구 유실될 수 있으므로, 이 순서를 반드시 지킨다.
- 알람 이벤트는 별도 테이블/컬렉션에 별도 보관 (감사 추적 목적, raw 데이터와 다른 보존 정책 적용 가능).
- **연속 다운샘플링(continuous aggregate):** 100 포인트 × 5초 주기 기준 raw 데이터는 연간 약
  6.3억 행(100 × 12/분 × 60 × 24 × 365)이 누적된다. 대시보드의 "1개월 추이" 같은 장기 조회가 raw를
  직접 스캔하면 응답 지연이 커지므로, TSDB의 continuous aggregate(TimescaleDB) 또는 downsampling
  task(InfluxDB)로 1분/1시간 단위 사전 집계를 만든다. 장기 차트는 사전 집계 테이블만 조회해
  수백만 행이 아닌 수천~수만 행만 스캔한다.

## Deployment & Update Strategy

- Edge Collector는 **컨테이너 이미지(Docker)**로 패키징한다. 현장 장비에는 컨테이너 런타임 + 경량
  에이전트만 설치하고, 실제 애플리케이션은 원격에서 새 이미지를 pull 받아 재시작하는 방식으로 갱신한다.
- 10대 PLC 규모에서 향후 사이트/PLC가 늘어나도 동일한 배포 방식이 그대로 확장된다.
- 현장 재방문 없이 버전을 올릴 수 있어, 알람/버퍼 로직에 버그가 발견됐을 때 대응 시간이 짧다.
- 초기 구축 시 이미지 빌드·배포용 최소 CI/CD 파이프라인(레지스트리 push + 원격 pull 트리거)이 필요 —
  Effort Estimate에 반영.
- **보안 경계 명확화:** 이미지 pull은 엣지 수집기가 **주기적으로 자기 스스로 레지스트리에 요청을
  거는 아웃바운드 연결**이다. "Network / Deployment Topology"에서 정의한 "아웃바운드 전용" 보안
  모델과 동일한 신뢰 경계이며, 별도의 인바운드 포트를 열지 않는다.

## Acceptance Criteria

1. PLC 10대 × 태그 10개(총 100포인트)를 5초 주기로 빠짐없이 수집해 클라우드 TSDB에 적재한다
   (수집 성공률 ≥ 99.5%, `seq` 연속성으로 검증). **성공률 분모는 게이트웨이 다운타임(Issue 3의
   `gateway_down` 상태)을 제외한 시간 기준으로 계산한다** — 게이트웨이 자체 장애는 별도 SLA로
   추적하고, 수집기 성능 지표를 오염시키지 않는다.
2. 수집기가 값을 감지한 시점부터 **클라우드 TSDB 적재까지** 지연이 5초를 초과하지 않는다 (P95 기준).
   대시보드 프론트엔드 자체의 렌더링/조회 지연은 이 프로젝트 범위 밖이며, 대시보드 팀과 별도로
   SLA를 합의한다 (대시보드가 어떻게 구현될지는 미정이므로, 이 문서가 대시보드 자체의 지연을
   보장할 수 없음을 명시).
3. 임계치를 벗어난 값 발생 시 3초 이내 알람 이벤트가 생성된다 (hysteresis 적용 후에도 유지).
4. 클라우드 연결 장애를 인위적으로 발생시켜도 로컬 버퍼에 데이터 유실 없이 저장되고, 복구 후
   알람이 밀린 readings보다 먼저, 누락분이 순서대로 재전송된다.
5. 1년 경과 데이터가 copy-then-verify-then-delete 절차로 안전하게 Parquet로 이전되고, 검증
   실패 시 Hot store의 원본 데이터가 삭제되지 않는다.
6. OPC UA 게이트웨이 재시작/재연결 시 수집기가 수동 개입 없이 자동 재구독하며, 게이트웨이
   자체 장애 시 `gateway_down` 상태를 대시보드에서 "설비 정상"과 구분해 표시한다.
7. Class A로 분류된 태그가 "태그 온보딩 워크플로우"의 병행 검증을 통과해 `status=LIVE`로
   전환되며, `status=COMMISSIONING`인 태그는 값이 수집·저장되더라도 알람 평가에는 포함되지
   않는다.

## Testing Plan

| Layer | 내용 | 개수 |
|---|---|---|
| Unit | 임계치 평가 로직(hysteresis 경계값 포함), seq 연속성 검증, deadband 필터, 결정적 alarm_id 생성 | +9 |
| Integration | OPC UA 시뮬레이터 ↔ 수집기 ↔ 로컬 버퍼 ↔ 목업 클라우드 API, tag_config since_version pull | +5 |
| Failure injection | 네트워크 단절/복구(우선순위 큐 검증 포함), 게이트웨이 재시작, 게이트웨이 heartbeat 끊김, 디스크 풀 | +4 |
| E2E | 알람 발생 → 대시보드 반영까지 지연 측정, 게이트웨이 다운 → "데이터 없음" 표시(설비 정상과 구분) | +3 |

## Effort Estimate (개략)

- OPC UA 클라이언트 + 구독/재연결/게이트웨이 heartbeat 감지: 3.5일
- 로컬 버퍼(SQLite) + 우선순위 큐 + 재전송 로직: 2.5일
- 알람 평가 엔진(hysteresis 포함) + 설정 테이블 + config_version pull: 2일
- 클라우드 수신 API + 중복 응답 계약 + TSDB 적재 + continuous aggregate: 2.5일
- Parquet 아카이빙 배치 잡: 1일
- 컨테이너화 + 원격 배포 파이프라인: 1.5일
- 대시보드 연동(기존 대시보드 프레임워크 가정): 2일
- 테스트(위 표): 3일

## Rollback Plan

- 엣지 수집기는 기존 수기 기록 프로세스를 대체하지 않고 **병행 운영**으로 시작 — 문제 발생 시 즉시 수집기만 중단해도 현장 운영에 영향 없음.
- 클라우드 수신 API는 버전(`/v1/`)을 명시해 스키마 변경 시 하위 호환 유지.

## Out of Scope (명시적 제외)

- PLC 제어/쓰기(write-back) 기능
- 예지보전 ML 모델 자체 (이상 탐지 알고리즘)
- 다중 사이트 통합
- **Class B 포인트의 센서/변환기 설치 및 PLC I/O 배선** — "Data Source Digitization" 절 참조.
  하드웨어 투자가 필요한 작업으로 설비팀/전기팀 소관이며, 본 프로젝트는 하드웨어 설치가 끝난
  뒤의 소프트웨어 온보딩(태그 등록·검증)만 담당한다.
- MES/ERP 연동 API 상세 스펙 — `TODOS.md` 참조, MES 팀 요구사항 확정 후 진행
- OPC UA/클라우드 TLS 인증서 발급·회전 절차 — `TODOS.md` 참조, 보안팀 PKI 정책 확정 후 진행
- 대시보드 프론트엔드 자체의 구현/지연 보장 — 이 프로젝트는 클라우드 TSDB 적재까지만 책임지며, 대시보드 팀과 별도 SLA 합의 필요

## What Already Exists

- PLC ↔ OPC UA 게이트웨이: 이미 존재 (OT망). 이 프로젝트는 게이트웨이를 재구축하지 않고 그 위에 클라이언트로 접속한다.
- 그 외 수집기/로컬 버퍼/클라우드 수신·저장/대시보드 연동은 모두 신규 — 재사용 가능한 기존 코드 없음 (완전 그린필드로 확인됨, Step 0).

## Worktree Parallelization Strategy

| Step | 모듈 | 의존성 |
|---|---|---|
| Edge Collector (OPC UA + 로컬 버퍼 + 알람 엔진) | `collector/` | — |
| Cloud Ingestion API + TSDB + continuous aggregate | `cloud-api/` | — |
| 배포/CI-CD 파이프라인 | `deploy/` | — |
| Parquet 아카이빙 배치 잡 | `cloud-api/archiving/` | Cloud API의 TSDB 스키마 확정 후 |
| 대시보드 연동 | (기존 대시보드 리포) | Cloud API 엔드포인트 확정 후 |

Lane A(Edge Collector) / Lane B(Cloud API+TSDB) / Lane C(배포 파이프라인)는 서로 독립이라 동시 착수 가능. Lane D(Parquet 아카이빙), Lane E(대시보드 연동)는 Lane B의 API 계약(스키마·엔드포인트)이 먼저 확정돼야 하지만, 스키마만 조기 합의하면 목업 기반으로 병행 가능. 디렉터리가 분리돼 있어 충돌 우려 모듈 없음.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 실행 안 함 (`/spec`으로 Why/Scope 인터뷰 대체) |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | git 저장소 없어 codex 미실행, Claude 서브에이전트로 대체 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 8개 이슈(아키텍처 4, 코드품질 2, 테스트 1, 성능 1) + 1 critical gap(Parquet 부분실패) — 전부 해결 및 문서 반영 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 대시보드 UI는 이 프로젝트 범위 밖 (별도 SLA) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 실행 안 함 |

**CROSS-MODEL:** Outside voice(Claude 서브에이전트)가 5개 전략적 갭 발견 — 저장소 아키텍처 과설계 논쟁 1건(사용자가 원안 유지 선택), 나머지 4건(대시보드 AC 재정의, 배포 보안 경계 문서화, 알람 config_version 추적, 99.5% 분모 정의)은 모두 수정 반영.

**VERDICT:** ENG CLEARED — 8개 리뷰 이슈 + 5개 outside voice 발견사항 모두 해결 및 문서 반영. 구현 착수 가능. CEO/Design/DX 리뷰는 선택사항으로 미실행 (필요 시 추가 진행).

**UNRESOLVED DECISIONS:**
- TODOS.md #1 (MES/ERP 연동 API 스펙) — MES 팀 요구사항 확정 후 재검토
- TODOS.md #2 (인증서 발급/회전 절차) — 보안팀 PKI 정책 확정 후 재검토
