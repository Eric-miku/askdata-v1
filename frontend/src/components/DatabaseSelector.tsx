import { Alert, Select, Spin } from "antd";
import { useEffect, useState } from "react";
import { listDatabases, type DatabaseInfo } from "../api/query";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function DatabaseSelector({ value, onChange }: Props) {
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);

    listDatabases()
      .then((items) => {
        if (!alive) {
          return;
        }
        setDatabases(items);
        if (!value && items.length > 0) {
          onChange(items[0].id);
        }
      })
      .catch((err) => {
        if (alive) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (alive) {
          setLoading(false);
        }
      });

    return () => {
      alive = false;
    };
  }, [onChange, value]);

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="数据库列表加载失败"
        description="请确认后端服务已启动在 http://127.0.0.1:8000"
      />
    );
  }

  return (
    <Select
      style={{ width: 320 }}
      placeholder={loading ? "正在加载数据库..." : "选择数据库"}
      value={value || undefined}
      onChange={onChange}
      notFoundContent={loading ? <Spin size="small" /> : "暂无数据库"}
      options={databases.map((database) => ({
        label: database.name || database.id,
        value: database.id,
      }))}
    />
  );
}
