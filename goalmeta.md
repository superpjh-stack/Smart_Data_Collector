# Smart Data Collector — /goal 메타프롬프트

> 이 문서는 `/goal` (ultragoal) 실행 시 입력으로 사용하기 위해 현재 남아있는 작업들을 정리한 메타프롬프트다.
> 배경/이미 끝난 것/설계 결정은 `progress.md`와 `docs/smart-data-collector-plan.md`(+ GSTACK REVIEW REPORT)에 있으므로 재검토하지 말 것.

## Context (읽고 시작할 것)

- 9-task 핵심 구현(수집기 + cloud-api ingestion/알람/버퍼/전송 + 유닛/통합 테스트)은 **완료** 상태 (32/32 테스트 통과, 2026-07-15 기준).
- PDCA 상태: `cloud_api`, `collector`, `tests`, `scripts` 4개 feature 모두 `do` 단계에 머물러 있고 Check(gap analysis)/Act 단계로 아직 진입하지 않음.
- Git 저장소 없음 (`git rev-parse` 실패, 확인됨).
- 재확정된 핵심 설계 결정(재검토 금지): Python 단일 스택, 결정론적 alarm_id, 알람 우선 드레인 버퍼, 클라이언트측 deadband, heartbeat 기반 게이트웨이 다운 감지, 200+{inserted,duplicates} 계약(409 없음), tag_config는 since_version 폴링, Parquet 아카이빙은 copy→verify→delete, TSDB+연속집계+Parquet 구조 유지.

## Goal — 남은 작업 목록 (우선순위 순)

### 1. PDCA Check 단계 진입 — Gap Analysis
- `cloud_api`, `collector` 두 feature에 대해 Design 문서 대비 구현 gap 분석 실행 (design 문서가 없다면 먼저 현재 구현 기준으로 역산 정리 후 진행).
- matchRate 산출, 90% 미만이면 `iterate` 단계로 자동 개선.

### 2. 배포 파이프라인 구축 (plan 문서 "Deployment & Update Strategy" 섹션 기준)
- `collector`, `cloud-api` 각각 Docker 이미지 패키징.
- 엣지 배포된 collector에 대한 원격 업데이트(remote-update) 절차/메커니즘 설계 및 구현.

### 3. Parquet 아카이빙 배치잡
- 1년 보관 정책에 따른 TSDB → Parquet 아카이빙 잡 구현.
- **반드시 copy → verify → delete 순서** (write-then-delete 금지, review에서 지적된 critical gap).

### 4. 환경 안정화 (개발 편의성, 선택)
- `tag_config` 시딩이 pytest 실행마다 초기화되는 문제 — dev 환경에서 seed와 test DB를 분리하거나, 매뉴얼 테스트 전 자동 reseed 훅 추가 검토.
- Git 저장소 초기화 여부 결정 (현재 미생성 상태).

## 명시적으로 보류/제외 (지금 진행하지 말 것)

- **MES/ERP 연동 API 상세 스펙** — MES팀의 실제 데이터 요구사항이 확정되지 않음. `TODOS.md` 참고, 요구사항 확정 전까지 설계 금지.
- **OPC UA / 클라우드 TLS 인증서 발급·회전 절차** — 사내 PKI/보안팀 정책 확정 대기 중. `TODOS.md` 참고.
- **대시보드 연동** — plan 문서상 명시적으로 범위 밖(별도 SLA).

## 완료 기준 (Definition of Done)

- [ ] cloud_api, collector 두 feature의 gap analysis 완료 및 matchRate >= 90%
- [ ] collector, cloud-api Docker 이미지 빌드 가능, 원격 업데이트 절차 문서화
- [ ] Parquet 아카이빙 배치잡 구현 + copy-verify-delete 순서 테스트로 검증
- [ ] 위 항목들에 대해 PDCA report 생성 (`/pdca report`)
