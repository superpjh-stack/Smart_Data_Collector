# TODOS

## MES/ERP 연동 API 상세 스펙 정의

**What:** 대시보드 옆 다이어그램에만 존재하는 "MES/ERP 연동 API"의 실제 데이터 형식·인증 방식·엔드포인트를 정의.

**Why:** 예지보전 데이터를 외부 분석/MES 시스템이 소비하려면 명시적 API 계약이 필요하지만, 현재는 아키텍처 다이어그램에 위치만 있고 스펙이 없음.

**Pros:** MES 팀과 통합할 때 재작업 없이 바로 연결 가능. 데이터 소유권/접근 제어를 API 레벨에서 명확히 할 수 있음.

**Cons:** 지금 정의하면 MES 시스템의 실제 요구사항을 모르는 상태에서 추측성 설계가 될 위험. 요구사항이 바뀌면 다시 설계해야 함.

**Context:** `docs/smart-data-collector-plan.md`의 Acceptance Criteria에는 MES 연동이 포함되어 있지 않음. 1차 MVP는 Hot Store(TSDB)에 대한 표준 쿼리 API만으로 충분할 수 있음 — MES 팀의 실제 요구사항이 확정된 후 이 TODO를 다시 열어 진행.

**Depends on:** MES/ERP 팀의 데이터 요구사항 확정 (담당 조직 미정).

---

## OPC UA / 클라우드 TLS 인증서 발급·회전 절차 정의

**What:** OPC UA 클라이언트 인증서와 엣지-클라우드 간 TLS 상호인증 인증서의 최초 발급 및 만료 전 자동/수동 회전 절차.

**Why:** 인증서가 만료되면 데이터 수집이 전면 중단된다. 이슈3(게이트웨이 heartbeat)로 "네트워크/게이트웨이 장애"는 구분되지만, 인증서 만료로 인한 중단은 다른 원인이라 별도 감지·알람·복구 절차가 필요하다. 예지보전 시스템에서 조용히 멈추는 장애가 가장 위험한 유형.

**Pros:** 운영 안정성 확보, 인증서 만료로 인한 전체 중단 사고 사전 예방.

**Cons:** 사내 PKI/보안팀의 정책 확인이 먼저 필요해 지금 세부 설계를 확정하기 어려움.

**Context:** `docs/smart-data-collector-plan.md`의 "Network / Deployment Topology" 절에 Sign & Encrypt, TLS 상호인증을 사용한다고만 되어 있고 인증서 lifecycle 관리는 명시되지 않음.

**Depends on:** 사내 보안팀의 PKI 정책 확정.
