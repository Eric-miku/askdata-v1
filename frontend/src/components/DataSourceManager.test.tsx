import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  createManagedDataSource: vi.fn(),
  createPermissionPolicy: vi.fn(),
  deleteManagedDataSource: vi.fn(),
  deletePermissionPolicy: vi.fn(),
  getManagedDataSourceSchema: vi.fn(),
  listManagedDataSources: vi.fn(),
  listPermissionPolicies: vi.fn(),
  setManagedDataSourceStatus: vi.fn(),
  syncManagedDataSource: vi.fn(),
  testManagedDataSource: vi.fn(),
}));

vi.mock("../api/query", () => api);

import DataSourceManager from "./DataSourceManager";


describe("DataSourceManager", () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset());
    api.listManagedDataSources.mockResolvedValue([{
      id: "sales",
      name: "销售库",
      kind: "sqlite",
      path: "/safe/sales.sqlite",
      enabled: true,
      health: "healthy",
      table_count: 1,
      index_count: 2,
      schema_fingerprint: "abcdef1234567890",
      schema_changed: true,
      schema_change_summary: {
        changed: true,
        initial_sync: false,
        tables_added: [],
        tables_removed: [],
        tables_changed: ["orders"],
      },
      last_synced_at: 1,
      created_at: 1,
      updated_at: 1,
    }]);
    api.listPermissionPolicies.mockResolvedValue([]);
    api.getManagedDataSourceSchema.mockResolvedValue({
      source_id: "sales",
      fingerprint: "abcdef1234567890",
      synced_at: 1,
      change_summary: {
        changed: true,
        initial_sync: false,
        tables_added: [],
        tables_removed: [],
        tables_changed: ["orders"],
      },
      catalog: {
        dialect: "sqlite",
        fingerprint: "abcdef1234567890",
        table_count: 1,
        column_count: 2,
        index_count: 2,
        tables: [{
          name: "orders",
          ddl: "CREATE TABLE orders(id INTEGER PRIMARY KEY, amount REAL)",
          columns: [
            { name: "id", type: "INTEGER", nullable: true, default: null, primary_key_position: 1 },
            { name: "amount", type: "REAL", nullable: true, default: null, primary_key_position: 0 },
          ],
          primary_key: ["id"],
          foreign_keys: [],
          indexes: [
            { name: "idx_a", unique: false, columns: ["amount"] },
            { name: "idx_b", unique: true, columns: ["id"] },
          ],
        }],
      },
    });
  });

  it("shows schema fingerprint and expands the persisted catalog", async () => {
    const user = userEvent.setup();
    render(<DataSourceManager open onClose={vi.fn()} onChanged={vi.fn()} />);

    expect(await screen.findByText("销售库")).toBeVisible();
    expect(screen.getByText(/1 张表 · 2 个索引/)).toBeVisible();
    expect(screen.getByText(/Schema abcdef123456 · 检测到结构变化/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "查看 Catalog" }));
    await waitFor(() => expect(api.getManagedDataSourceSchema).toHaveBeenCalledWith("sales"));
    expect(screen.getByText("orders")).toBeVisible();
    expect(screen.getByText("2 字段 · 2 索引")).toBeVisible();
    expect(screen.getByRole("button", { name: "收起 Catalog" })).toBeVisible();
  });

  it("saves a table row filter as part of the permission policy", async () => {
    const user = userEvent.setup();
    api.createPermissionPolicy.mockResolvedValue({});
    render(<DataSourceManager open onClose={vi.fn()} onChanged={vi.fn()} />);
    await screen.findByText("销售库");

    await user.type(screen.getByLabelText("用户 ID"), "alice");
    await user.type(screen.getAllByLabelText("数据源 ID")[1], "sales");
    await user.type(screen.getByLabelText("表名（可选）"), "orders");
    await user.type(screen.getByLabelText("行过滤（可选）"), "region = '华东'");
    await user.click(screen.getByRole("button", { name: "保存授权" }));

    await waitFor(() => expect(api.createPermissionPolicy).toHaveBeenCalledWith({
      user_id: "alice",
      database_id: "sales",
      table_name: "orders",
      field_name: null,
      can_query: true,
      can_export: true,
      row_filter: "region = '华东'",
    }));
  });
});
