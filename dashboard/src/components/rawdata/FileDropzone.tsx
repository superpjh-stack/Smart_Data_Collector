import { useRef, useState } from "react";

interface Props {
  accept: string;
  multiple: boolean;
  files: File[];
  onChange: (files: File[]) => void;
  label?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

export function FileDropzone({ accept, multiple, files, onChange, label }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const incoming = Array.from(list);
    onChange(multiple ? [...files, ...incoming] : incoming.slice(0, 1));
  }

  function removeAt(idx: number) {
    onChange(files.filter((_, i) => i !== idx));
  }

  return (
    <div className="rd-dropzone-wrap">
      <div
        className={`rd-dropzone ${dragOver ? "drag" : ""}`}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          addFiles(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
      >
        <span className="rd-dropzone-icon" aria-hidden="true">📄</span>
        <span className="rd-dropzone-text">
          {label ?? `파일을 드래그하거나 클릭하여 선택${multiple ? " (다중 가능)" : ""}`}
        </span>
        <span className="rd-dropzone-accept">{accept.split(",").join(", ")}</span>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          hidden
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="rd-file-list">
          {files.map((f, idx) => (
            <li key={`${f.name}-${idx}`} className="rd-file-row">
              <span className="rd-file-name">{f.name}</span>
              <span className="rd-file-size num">{formatBytes(f.size)}</span>
              <button
                type="button"
                className="rd-file-remove"
                onClick={() => removeAt(idx)}
                aria-label={`${f.name} 제거`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
