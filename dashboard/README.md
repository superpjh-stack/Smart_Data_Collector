# dashboard — Smart Data Collector 운영 현황판

React + Vite + TypeScript SPA. `cloud_api`의 조회 엔드포인트(`/readings/v1`, `/alarms/v1`,
`/status/v1/gateways*`)를 폴링해서 PLC 상태, 알람, 게이트웨이 이력, 수집 처리량을 보여준다.

컬렉터가 실제로 보내는 스키마에서 화면 상태를 그대로 유도한다(`src/derive.ts`) — 게이트웨이가
`gateway_down`이면 그 PLC의 다른 모든 신호보다 우선해서 오프라인으로 표시한다. 장비 이상과
통신 두절을 구분하는 것이 프로젝트의 핵심 설계 결정(Issue 3)이기 때문이다.

## 로컬 실행

전제조건: `cloud_api`가 떠 있어야 한다 (`docker compose up -d --build`, 루트에서).

```bash
npm install
npm run dev       # http://localhost:5173
```

기본적으로 `http://localhost:8000`의 cloud_api를 호출한다. 다른 주소를 쓰려면
`.env.example`을 `.env`로 복사해서 `VITE_API_BASE_URL`을 바꾼다.

cloud_api 쪽 CORS는 `DASHBOARD_ORIGINS` 환경변수로 제어된다(기본값이 이미
`http://localhost:5173`이라 로컬 개발에서는 별도 설정이 필요 없다).

## 구조

```
src/
  api.ts                fetch 클라이언트 — cloud_api의 GET 엔드포인트만 호출 (쓰기 없음)
  types.ts              cloud_api 스키마와 1:1 대응하는 타입 + 화면 전용 파생 타입(PlcSummary)
  derive.ts             readings + alarms + gateways 세 폴링 결과를 PLC별 화면 상태로 합성
  hooks/usePolling.ts   일정 주기로 재요청, 느슨한 응답이 최신 응답을 덮어쓰지 않도록 세대 체크
  components/
    PlcGrid.tsx          개요 탭 — PLC 카드 그리드
    AlarmList.tsx        알람 탭
    GatewayHistory.tsx   게이트웨이 이력 탭
    Sparkline.tsx        PLC 카드용 캔버스 스파크라인 (readings_1min 기반)
    ThroughputChart.tsx  하단 처리량 차트
```

## 폴링 주기

| 데이터 | 주기 | 비고 |
|---|---|---|
| 최신 readings, 알람, 게이트웨이 스냅샷 | 5초 | 개요/알람 탭의 실시간성 기준 |
| PLC별 1분 봉 히스토리(스파크라인) | primary tag 집합이 바뀔 때 재계산 | `readings_1min` 연속 집계 조회 |
| 게이트웨이 이력 | 15초 | 이벤트 빈도가 낮아 더 느슨하게 |
| 처리량 차트 | 10초 | |

실시간 알림(WebSocket/SSE)은 없다 — cloud_api가 아직 폴링 기반 조회만 제공하기 때문. 필요해지면
`/status/v1` 계열에 SSE 스트림을 추가하는 것으로 확장 가능하다.
