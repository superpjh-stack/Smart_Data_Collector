import type { RawDataSourceType } from "../../types";

export const SOURCE_TYPE_LABEL: Record<RawDataSourceType, string> = {
  excel: "엑셀",
  word: "워드",
  scanned_pdf: "스캔 PDF/이미지",
  equipment_layout: "설비 배치",
  db_sql: "DB/SQL",
};
