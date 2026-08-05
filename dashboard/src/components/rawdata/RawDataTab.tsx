import { useMemo, useState } from "react";
import { createRawDataSources, deleteRawDataSource, fetchRawDataSources, fetchRawDataSummary } from "../../api";
import type { CreateRawDataSourceInput } from "../../api";
import { usePolling } from "../../hooks/usePolling";
import type { RawDataSource, RawDataSourceType } from "../../types";
import { SourceDetailPanel } from "./SourceDetailPanel";
import { SourceFilterBar } from "./SourceFilterBar";
import { SourceList, type OptimisticRow } from "./SourceList";
import { SourceRegisterPanel } from "./SourceRegisterPanel";
import { SourceSummaryCards } from "./SourceSummaryCards";

let clientIdSeq = 0;

// Stable reference so `allSources` doesn't look like a new array on every
// render before the first list poll resolves (avoids an exhaustive-deps
// false positive on the useMemo below).
const EMPTY_SOURCES: RawDataSource[] = [];

/** 탭 컨테이너 — 목록/필터/등록패널 상태를 자체적으로 관리한다
 * (ui-design.md §5). 검색은 클라이언트 사이드 필터링으로 충분하다는
 * ui-design.md §2.6 방침에 따라, 목록은 필터 없이 한 번에 가져와
 * 타입/검색 조건을 메모리에서 적용한다. */
export function RawDataTab() {
  const [panelOpen, setPanelOpen] = useState(false);
  const [filter, setFilter] = useState<RawDataSourceType | "all">("all");
  const [search, setSearch] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [optimisticRows, setOptimisticRows] = useState<OptimisticRow[]>([]);
  const [selected, setSelected] = useState<RawDataSource | null>(null);

  const summary = usePolling(fetchRawDataSummary, 15000);
  const list = usePolling(() => fetchRawDataSources({ limit: 200 }), 15000);

  const allSources = list.data?.sources ?? EMPTY_SOURCES;

  const filteredSources = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allSources.filter((s) => {
      if (filter !== "all" && s.source_type !== filter) return false;
      if (!q) return true;
      return s.name.toLowerCase().includes(q) || (s.equipment_tag ?? "").toLowerCase().includes(q);
    });
  }, [allSources, filter, search]);

  const visibleOptimisticRows = useMemo(
    () => optimisticRows.filter((r) => filter === "all" || r.sourceType === filter),
    [optimisticRows, filter],
  );

  async function handleCreate(input: CreateRawDataSourceInput) {
    setFormError(null);
    setSubmitting(true);

    const clientId = `pending-${++clientIdSeq}`;
    const fileLabel =
      input.files && input.files.length > 0
        ? input.files.length === 1
          ? input.files[0].name
          : `${input.files[0].name} 외 ${input.files.length - 1}건`
        : null;

    setOptimisticRows((prev) => [
      {
        clientId,
        sourceType: input.source_type,
        name: input.name,
        fileLabel,
        equipmentTag: input.equipment_tag ?? null,
        registeredBy: input.registered_by,
        status: "uploading",
      },
      ...prev,
    ]);

    try {
      await createRawDataSources(input);
      setOptimisticRows((prev) => prev.filter((r) => r.clientId !== clientId));
      setPanelOpen(false);
      summary.refetch();
      list.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.";
      setOptimisticRows((prev) =>
        prev.map((r) =>
          r.clientId === clientId ? { ...r, status: "error", errorMessage: message, retryInput: input } : r,
        ),
      );
      setFormError(`업로드에 실패했습니다: ${message}`);
    } finally {
      setSubmitting(false);
    }
  }

  function handleDismissError(clientId: string) {
    setOptimisticRows((prev) => prev.filter((r) => r.clientId !== clientId));
  }

  function handleRetry(clientId: string) {
    const row = optimisticRows.find((r) => r.clientId === clientId);
    setOptimisticRows((prev) => prev.filter((r) => r.clientId !== clientId));
    if (row?.retryInput) {
      handleCreate(row.retryInput);
    }
  }

  async function handleDelete(id: number) {
    try {
      await deleteRawDataSource(id);
    } finally {
      setSelected(null);
      summary.refetch();
      list.refetch();
    }
  }

  function resetFilters() {
    setFilter("all");
    setSearch("");
  }

  const isEmpty = allSources.length === 0 && optimisticRows.length === 0;
  const isFilteredEmpty = !isEmpty && filteredSources.length === 0 && visibleOptimisticRows.length === 0;

  return (
    <div className="rd-tab">
      <SourceSummaryCards
        summary={summary.data}
        activeFilter={filter}
        onSelect={(type) => setFilter((prev) => (prev === type ? "all" : type))}
      />

      <div className="rd-toolbar">
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setPanelOpen((v) => !v);
            setFormError(null);
          }}
        >
          {panelOpen ? "등록 패널 닫기" : "+ 새 소스 등록"}
        </button>
      </div>

      {panelOpen && (
        <SourceRegisterPanel
          submitting={submitting}
          formError={formError}
          onCancel={() => setPanelOpen(false)}
          onSubmit={handleCreate}
        />
      )}

      <SourceFilterBar active={filter} onChange={setFilter} search={search} onSearchChange={setSearch} />

      {list.error && (
        <div className="conn-error">원본 데이터 목록을 불러올 수 없습니다 ({list.error.message}).</div>
      )}

      {isEmpty ? (
        <p className="empty-note">
          아직 등록된 원본 데이터가 없습니다. 위 &lsquo;+ 새 소스 등록&rsquo; 버튼으로 첫 파일을 업로드하거나
          소스를 등록하세요.
        </p>
      ) : isFilteredEmpty ? (
        <p className="empty-note">
          선택한 조건에 맞는 항목이 없습니다.{" "}
          <button type="button" className="rd-link-btn" onClick={resetFilters}>
            필터 초기화
          </button>
        </p>
      ) : (
        <SourceList
          sources={filteredSources}
          optimisticRows={visibleOptimisticRows}
          onSelect={setSelected}
          onRetry={handleRetry}
          onDismissError={handleDismissError}
        />
      )}

      {selected && <SourceDetailPanel source={selected} onClose={() => setSelected(null)} onDelete={handleDelete} />}
    </div>
  );
}
