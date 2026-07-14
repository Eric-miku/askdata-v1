import { Empty, Table, Typography } from "antd";
import type { ReactNode } from "react";
import type { ColumnsType } from "antd/es/table";
import type { QueryCellValue } from "../types/query";
import { ChevronIcon } from "./Icons";

interface ResultTableProps {
  columns?: string[] | null;
  rows?: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  loading?: boolean;
}

type TableRecord = {
  key: string;
  __rowIndex: number;
  [column: string]: QueryCellValue | string | number;
};

function normalizeColumnName(name: string, index: number): string {
  const trimmed = name.trim();
  return trimmed || `column_${index + 1}`;
}

function formatCellValue(value: QueryCellValue): ReactNode {
  if (value === null || value === undefined) {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }

  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }

  if (Array.isArray(value) || typeof value === "object") {
    return (
      <Typography.Text code className="result-table__json-cell">
        {JSON.stringify(value)}
      </Typography.Text>
    );
  }

  return String(value);
}

function compareCellValue(a: QueryCellValue, b: QueryCellValue): number {
  if (a === b) {
    return 0;
  }

  if (a === null || a === undefined) {
    return 1;
  }

  if (b === null || b === undefined) {
    return -1;
  }

  if (typeof a === "number" && typeof b === "number") {
    return a - b;
  }

  const aNumber = Number(a);
  const bNumber = Number(b);
  if (!Number.isNaN(aNumber) && !Number.isNaN(bNumber)) {
    return aNumber - bNumber;
  }

  return String(a).localeCompare(String(b), "zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
}

function buildDataSource(
  columnNames: string[],
  rows: Record<string, QueryCellValue>[] | QueryCellValue[][],
): TableRecord[] {
  return rows.map((row, rowIndex) => {
    const record: TableRecord = {
      key: String(rowIndex),
      __rowIndex: rowIndex + 1,
    };

    columnNames.forEach((columnName, columnIndex) => {
      record[columnName] = Array.isArray(row) ? row[columnIndex] : row[columnName];
    });

    return record;
  });
}

export function ResultTable({ columns, rows, loading = false }: ResultTableProps) {
  const hasColumns = Boolean(columns?.length);
  const hasRows = Boolean(rows?.length);

  if (!loading && (!hasColumns || !hasRows)) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无表格数据" />;
  }

  const columnNames = (columns ?? []).map(normalizeColumnName);
  const dataSource = buildDataSource(columnNames, rows ?? []);

  const tableColumns: ColumnsType<TableRecord> = [
    {
      title: "#",
      dataIndex: "__rowIndex",
      key: "__rowIndex",
      width: 72,
      fixed: "left",
      sorter: (a, b) => a.__rowIndex - b.__rowIndex,
    },
    ...columnNames.map((columnName) => ({
      title: columnName,
      dataIndex: columnName,
      key: columnName,
      ellipsis: true,
      sorter: (a: TableRecord, b: TableRecord) =>
        compareCellValue(
          a[columnName] as QueryCellValue,
          b[columnName] as QueryCellValue,
        ),
      render: (value: QueryCellValue) => formatCellValue(value),
    })),
  ];

  return (
    <Table<TableRecord>
      className="result-table"
      columns={tableColumns}
      dataSource={dataSource}
      loading={loading}
      size="middle"
      bordered
      scroll={{ x: "max-content" }}
      pagination={{
        showSizeChanger: {
          showSearch: false,
          suffixIcon: (
            <ChevronIcon className="result-table__page-size-chevron" />
          ),
          classNames: {
            popup: {
              root: "result-table__page-size-dropdown",
            },
          },
        },
        showTitle: false,
        pageSizeOptions: [10, 20, 50, 100],
        defaultPageSize: 10,
        showTotal: (total) => `共 ${total} 行`,
        itemRender: (_, type, originalElement) =>
          type === "jump-prev" || type === "jump-next" ? null : originalElement,
      }}
    />
  );
}
