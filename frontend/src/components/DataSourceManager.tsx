import { useEffect, useState } from "react";
import {
  createManagedDataSource,
  createPermissionPolicy,
  deleteManagedDataSource,
  deletePermissionPolicy,
  getManagedDataSourceSchema,
  listManagedDataSources,
  listPermissionPolicies,
  setManagedDataSourceStatus,
  syncManagedDataSource,
  testManagedDataSource,
} from "../api/query";
import type { ManagedDataSourceInput, PermissionPolicyInput } from "../types/query";
import type { SchemaCatalogSnapshot } from "../types/query";
import { CloseIcon } from "./Icons";

interface Props {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}

export default function DataSourceManager({ open, onClose, onChanged }: Props) {
  const [sources, setSources] = useState<Awaited<ReturnType<typeof listManagedDataSources>>>([]);
  const [policies, setPolicies] = useState<Awaited<ReturnType<typeof listPermissionPolicies>>>([]);
  const [form, setForm] = useState<ManagedDataSourceInput>({ id: "", name: "", kind: "sqlite", path: "", enabled: true });
  const [permissionForm, setPermissionForm] = useState<PermissionPolicyInput>({
    user_id: "", database_id: "", table_name: null, field_name: null,
    can_query: true, can_export: true, row_filter: null,
  });
  const [error, setError] = useState<string | null>(null);
  const [catalogs, setCatalogs] = useState<Record<string, SchemaCatalogSnapshot>>({});
  const load = async () => {
    try {
      const [nextSources, nextPolicies] = await Promise.all([listManagedDataSources(), listPermissionPolicies()]);
      setSources(nextSources); setPolicies(nextPolicies); setError(null);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  useEffect(() => { if (open) void load(); }, [open]);
  if (!open) return null;

  const create = async () => {
    if (!form.id || !form.name || !form.path) { setError("请填写 ID、名称和数据源位置"); return; }
    try {
      await createManagedDataSource(form);
      setForm({ id: "", name: "", kind: "sqlite", path: "", enabled: true });
      await load();
      onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const grant = async () => {
    if (!permissionForm.user_id || !permissionForm.database_id) { setError("请填写用户 ID 和数据源 ID"); return; }
    if (permissionForm.field_name && !permissionForm.table_name) { setError("字段级权限必须填写表名"); return; }
    try {
      await createPermissionPolicy(permissionForm);
      setPermissionForm({ user_id: "", database_id: "", table_name: null, field_name: null, can_query: true, can_export: true, row_filter: null });
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const toggleCatalog = async (sourceId: string) => {
    if (catalogs[sourceId]) {
      setCatalogs((current) => {
        const next = { ...current };
        delete next[sourceId];
        return next;
      });
      return;
    }
    try {
      const catalog = await getManagedDataSourceSchema(sourceId);
      setCatalogs((current) => ({ ...current, [sourceId]: catalog }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  return <div className="knowledge-modal" role="dialog" aria-modal="true" aria-label="数据源管理">
    <button type="button" className="knowledge-modal__scrim" aria-label="关闭数据源管理" onClick={onClose} />
    <section className="knowledge-panel data-source-panel">
      <header className="knowledge-panel__header"><div><strong>数据源管理</strong><span>接入、测试和同步受控 SQLite / MySQL 数据源</span></div><button type="button" className="icon-button" aria-label="关闭数据源管理" onClick={onClose}><CloseIcon /></button></header>
      <main className="data-source-content">
        {error ? <div className="workspace-alert" role="alert">{error}</div> : null}
        <section className="data-source-create">
          <label><span>数据源 ID</span><input value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} placeholder="finance" /></label>
          <label><span>显示名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="财务数据库" /></label>
          <label><span>类型</span><select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as ManagedDataSourceInput["kind"] })}><option value="sqlite">SQLite</option><option value="mysql">MySQL</option><option value="postgres">PostgreSQL</option></select></label>
          <label><span>{form.kind === "sqlite" ? "SQLite 相对路径" : "连接配置"}</span><input value={form.path} onChange={(event) => setForm({ ...form, path: event.target.value })} placeholder={form.kind === "sqlite" ? "financial/financial.sqlite" : "env:COMPANY_MYSQL_URL"} /></label>
          <button type="button" onClick={() => void create()}>新增数据源</button>
        </section>
        <section className="data-source-list">
          {sources.length ? sources.map((source) => <article key={source.id}>
            <header><div><strong>{source.name}</strong><code>{source.id}</code></div><span className={`is-${source.health}`}>{source.health === "healthy" ? "连接正常" : source.health === "unhealthy" ? "连接失败" : "未测试"}</span></header>
            <p>{source.kind} · {source.path}</p>
            <small>{source.table_count} 张表 · {source.index_count || 0} 个索引 · {source.last_synced_at ? `最近同步 ${new Date(source.last_synced_at * 1000).toLocaleString()}` : "尚未同步"}</small>
            {source.schema_fingerprint ? <small className="schema-fingerprint">Schema {source.schema_fingerprint.slice(0, 12)}{source.schema_changed ? " · 检测到结构变化" : " · 结构未变化"}</small> : null}
            {source.last_error ? <em>{source.last_error}</em> : null}
            <div>
              <button type="button" onClick={() => void testManagedDataSource(source.id).then(load)}>测试连接</button>
              <button type="button" onClick={() => void syncManagedDataSource(source.id).then(load).then(onChanged)}>同步 Schema</button>
              <button type="button" disabled={!source.schema_fingerprint} onClick={() => void toggleCatalog(source.id)}>{catalogs[source.id] ? "收起 Catalog" : "查看 Catalog"}</button>
              <button type="button" onClick={() => void setManagedDataSourceStatus(source.id, !source.enabled).then(load).then(onChanged)}>{source.enabled ? "禁用" : "启用"}</button>
              <button type="button" className="is-danger" onClick={() => void deleteManagedDataSource(source.id).then(load).then(onChanged)}>删除配置</button>
            </div>
            {catalogs[source.id] ? <div className="schema-catalog">
              {catalogs[source.id].catalog.tables.map((table) => <details key={table.name}>
                <summary><strong>{table.name}</strong><span>{table.columns.length} 字段 · {table.indexes.length} 索引</span></summary>
                <div className="schema-catalog__columns">
                  {table.columns.map((column) => <code key={column.name}>{column.name} <span>{column.type}</span>{column.primary_key_position ? " PK" : ""}</code>)}
                </div>
                {table.foreign_keys.length ? <small>{table.foreign_keys.length} 个外键关系</small> : null}
              </details>)}
            </div> : null}
          </article>) : <p className="muted-copy">暂无手动管理的数据源；BIRD 数据库仍会自动发现。</p>}
        </section>
        <section className="permission-manager">
          <header><strong>对象权限</strong><span>按用户授权数据源、表、字段及导出能力；配置首条策略后启用白名单模式。</span></header>
          <div className="permission-create">
            <label><span>用户 ID</span><input value={permissionForm.user_id} onChange={(event) => setPermissionForm({ ...permissionForm, user_id: event.target.value })} placeholder="alice" /></label>
            <label><span>数据源 ID</span><input value={permissionForm.database_id} onChange={(event) => setPermissionForm({ ...permissionForm, database_id: event.target.value })} placeholder="finance" /></label>
            <label><span>表名（可选）</span><input value={permissionForm.table_name ?? ""} onChange={(event) => setPermissionForm({ ...permissionForm, table_name: event.target.value || null })} placeholder="orders" /></label>
            <label><span>字段名（可选）</span><input value={permissionForm.field_name ?? ""} onChange={(event) => setPermissionForm({ ...permissionForm, field_name: event.target.value || null })} placeholder="amount" /></label>
            <label><span>行过滤（可选）</span><input value={permissionForm.row_filter ?? ""} onChange={(event) => setPermissionForm({ ...permissionForm, row_filter: event.target.value || null })} placeholder="region = '华东'" /></label>
            <label className="permission-check"><input type="checkbox" checked={permissionForm.can_query} onChange={(event) => setPermissionForm({ ...permissionForm, can_query: event.target.checked })} /><span>允许查询</span></label>
            <label className="permission-check"><input type="checkbox" checked={permissionForm.can_export} onChange={(event) => setPermissionForm({ ...permissionForm, can_export: event.target.checked })} /><span>允许导出</span></label>
            <button type="button" onClick={() => void grant()}>保存授权</button>
          </div>
          <div className="permission-list">
            {policies.length ? policies.map((policy) => <article key={policy.id}>
              <div><strong>{policy.user_id}</strong><code>{policy.database_id}{policy.table_name ? ` / ${policy.table_name}` : ""}{policy.field_name ? ` / ${policy.field_name}` : ""}</code></div>
              <span>{policy.can_query ? "可查询" : "禁查询"} · {policy.can_export ? "可导出" : "禁导出"}{policy.row_filter ? ` · 行过滤 ${policy.row_filter}` : ""}</span>
              <button type="button" className="is-danger" onClick={() => void deletePermissionPolicy(policy.id).then(load)}>撤销</button>
            </article>) : <p className="muted-copy">尚未配置策略，当前为本地开发兼容模式。</p>}
          </div>
        </section>
      </main>
    </section>
  </div>;
}
