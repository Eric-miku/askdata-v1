import { useEffect, useMemo, useState } from "react";
import {
  createKnowledgeEntry,
  deleteKnowledgeEntry,
  downloadKnowledgeExport,
  importKnowledgeEntries,
  listKnowledgeEntries,
  listKnowledgeVersions,
  publishKnowledgeEntry,
  rollbackKnowledgeEntry,
  updateKnowledgeEntry,
} from "../api/query";
import type { KnowledgeEntry, KnowledgeEntryInput } from "../types/query";
import { CloseIcon, SearchIcon } from "./Icons";

interface KnowledgeManagerProps {
  open: boolean;
  onClose: () => void;
}

const emptyEntry: KnowledgeEntryInput = {
  kind: "term",
  standard_name: "",
  definition: "",
  category: "",
  scope: "",
  status: "draft",
  aliases: [],
  mappings: [],
  formula: "",
  aggregation: "",
  unit: "",
  time_field: "",
  examples: [],
  changelog: "",
};

function toInput(entry: KnowledgeEntry): KnowledgeEntryInput {
  const { id: _id, version: _version, updated_by: _updatedBy, updated_at: _updatedAt, ...input } = entry;
  return input;
}

export default function KnowledgeManager({ open, onClose }: KnowledgeManagerProps) {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<KnowledgeEntryInput>(emptyEntry);
  const [search, setSearch] = useState("");
  const [versions, setVersions] = useState<KnowledgeEntry[]>([]);
  const [mappingDraft, setMappingDraft] = useState({ database_id: "", table: "", field: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = useMemo(() => entries.find((entry) => entry.id === selectedId) ?? null, [entries, selectedId]);

  const load = async (keyword = search) => {
    setLoading(true);
    setError(null);
    try {
      setEntries(await listKnowledgeEntries(keyword));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load("");
  }, [open]);

  useEffect(() => {
    if (!selected) {
      setVersions([]);
      return;
    }
    setForm(toInput(selected));
    void listKnowledgeVersions(selected.id).then(setVersions).catch(() => setVersions([]));
  }, [selected]);

  if (!open) return null;

  const save = async () => {
    if (!form.standard_name.trim()) {
      setError("请输入标准名称");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const saved = selectedId
        ? await updateKnowledgeEntry(selectedId, form)
        : await createKnowledgeEntry(form);
      await load();
      setSelectedId(saved.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setLoading(false);
    }
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const parsed: unknown = JSON.parse(await file.text());
      const rawEntries = Array.isArray(parsed)
        ? parsed
        : parsed && typeof parsed === "object" && "entries" in parsed
          ? (parsed as { entries: unknown }).entries
          : null;
      if (!Array.isArray(rawEntries)) throw new Error("导入文件必须是条目数组或包含 entries 数组的 JSON");
      const result = await importKnowledgeEntries(rawEntries as Array<Record<string, unknown>>);
      await load();
      if (result.failed) setError(`成功导入 ${result.imported} 条，失败 ${result.failed} 条：${result.errors[0]?.error ?? "请检查文件"}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="knowledge-modal" role="dialog" aria-modal="true" aria-label="业务术语管理">
      <button type="button" className="knowledge-modal__scrim" aria-label="关闭业务术语管理" onClick={onClose} />
      <section className="knowledge-panel">
        <header className="knowledge-panel__header">
          <div><strong>业务术语与指标</strong><span>维护别名、口径、示例和版本</span></div>
          <div className="knowledge-header-actions">
            <label><input type="file" accept="application/json,.json" onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = ""; }} />导入 JSON</label>
            <button type="button" onClick={() => void downloadKnowledgeExport("json")}>导出 JSON</button>
            <button type="button" className="icon-button" aria-label="关闭术语管理" onClick={onClose}><CloseIcon /></button>
          </div>
        </header>
        <div className="knowledge-layout">
          <aside className="knowledge-list">
            <form onSubmit={(event) => { event.preventDefault(); void load(); }} className="knowledge-search">
              <SearchIcon /><input aria-label="搜索术语" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称、定义或别名" />
            </form>
            <button type="button" className="knowledge-new" onClick={() => { setSelectedId(null); setForm(emptyEntry); setVersions([]); }}>+ 新建术语或指标</button>
            {loading && !entries.length ? <p className="muted-copy">加载中…</p> : null}
            {entries.map((entry) => (
              <button type="button" key={entry.id} className={`knowledge-list__item ${entry.id === selectedId ? "is-selected" : ""}`} onClick={() => setSelectedId(entry.id)}>
                <span><strong>{entry.standard_name}</strong><small>{entry.kind === "metric" ? "指标" : "术语"} · v{entry.version}</small></span>
                <em className={`is-${entry.status}`}>{entry.status === "published" ? "已发布" : entry.status === "disabled" ? "已禁用" : "草稿"}</em>
              </button>
            ))}
          </aside>
          <main className="knowledge-editor">
            {error ? <div className="workspace-alert" role="alert">{error}</div> : null}
            <div className="knowledge-form-grid">
              <label><span>类型</span><select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as "term" | "metric" })}><option value="term">业务术语</option><option value="metric">指标</option></select></label>
              <label><span>标准名称</span><input value={form.standard_name} onChange={(event) => setForm({ ...form, standard_name: event.target.value })} /></label>
              <label><span>分类</span><input value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} /></label>
              <label><span>适用范围</span><input value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })} /></label>
              <label className="is-wide"><span>定义</span><textarea value={form.definition} onChange={(event) => setForm({ ...form, definition: event.target.value })} /></label>
              <label className="is-wide"><span>别名（逗号分隔）</span><input value={form.aliases.join(", ")} onChange={(event) => setForm({ ...form, aliases: event.target.value.split(/[,，]/).map((value) => value.trim()).filter(Boolean) })} /></label>
              {form.kind === "metric" ? <>
                <label className="is-wide"><span>计算公式</span><input value={form.formula} onChange={(event) => setForm({ ...form, formula: event.target.value })} placeholder="SUM(amount)" /></label>
                <label><span>聚合方式</span><input value={form.aggregation} onChange={(event) => setForm({ ...form, aggregation: event.target.value })} /></label>
                <label><span>单位</span><input value={form.unit} onChange={(event) => setForm({ ...form, unit: event.target.value })} /></label>
                <label><span>时间字段</span><input value={form.time_field} onChange={(event) => setForm({ ...form, time_field: event.target.value })} /></label>
              </> : null}
              <fieldset className="knowledge-mapping is-wide">
                <legend>数据库字段映射</legend>
                <div>
                  <input aria-label="映射数据源" placeholder="数据源 ID" value={mappingDraft.database_id} onChange={(event) => setMappingDraft({ ...mappingDraft, database_id: event.target.value })} />
                  <input aria-label="映射表" placeholder="表名" value={mappingDraft.table} onChange={(event) => setMappingDraft({ ...mappingDraft, table: event.target.value })} />
                  <input aria-label="映射字段" placeholder="字段名" value={mappingDraft.field} onChange={(event) => setMappingDraft({ ...mappingDraft, field: event.target.value })} />
                  <button type="button" onClick={() => {
                    if (!mappingDraft.database_id || !mappingDraft.table || !mappingDraft.field) return;
                    setForm({ ...form, mappings: [...form.mappings, mappingDraft] });
                    setMappingDraft({ database_id: "", table: "", field: "" });
                  }}>添加映射</button>
                </div>
                {form.mappings.map((mapping, index) => <span key={`${String(mapping.database_id)}-${index}`}><code>{String(mapping.database_id)}.{String(mapping.table)}.{String(mapping.field)}</code><button type="button" aria-label={`删除映射 ${index + 1}`} onClick={() => setForm({ ...form, mappings: form.mappings.filter((_, itemIndex) => itemIndex !== index) })}>×</button></span>)}
              </fieldset>
              <label className="is-wide"><span>示例问法（每行一个）</span><textarea value={form.examples.join("\n")} onChange={(event) => setForm({ ...form, examples: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
              <label className="is-wide"><span>变更说明</span><input value={form.changelog} onChange={(event) => setForm({ ...form, changelog: event.target.value })} /></label>
            </div>
            <div className="knowledge-actions">
              <button type="button" className="is-primary" disabled={loading} onClick={() => void save()}>保存草稿</button>
              {selected ? <button type="button" disabled={loading} onClick={() => void publishKnowledgeEntry(selected.id).then(() => load())}>发布</button> : null}
              {selected ? <button type="button" className="is-danger" disabled={loading} onClick={() => void deleteKnowledgeEntry(selected.id).then(() => { setSelectedId(null); setForm(emptyEntry); void load(); })}>删除</button> : null}
            </div>
            {versions.length > 1 ? <section className="knowledge-versions"><strong>版本历史</strong>{versions.map((version, index) => <div key={`${version.version}-${index}`}><span>v{version.version} · {version.changelog || "未填写变更说明"}</span>{index ? <button type="button" onClick={() => void rollbackKnowledgeEntry(version.id, version.version).then(() => load())}>回滚到此版本</button> : <em>当前版本</em>}</div>)}</section> : null}
          </main>
        </div>
      </section>
    </div>
  );
}
