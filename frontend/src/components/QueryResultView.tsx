import { Alert, Collapse, Divider, Empty, Space, Typography } from "antd";
import type { QueryResponse } from "../types/query";
import { ResultTable } from "./ResultTable";

interface QueryResultViewProps {
  result?: QueryResponse | null;
  loading?: boolean;
}

export function QueryResultView({ result, loading = false }: QueryResultViewProps) {
  if (!result && !loading) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无查询结果" />;
  }

  return (
    <main className="query-result">
      {result?.error ? (
        <Alert
          className="query-result__alert"
          type="error"
          showIcon
          message="查询失败"
          description={result.error}
        />
      ) : null}

      <section className="query-result__section">
        <Typography.Title level={4}>回答</Typography.Title>
        <Typography.Paragraph className="query-result__answer">
          {result?.answer || (loading ? "查询中..." : "-")}
        </Typography.Paragraph>
      </section>

      {result?.sql ? (
        <section className="query-result__section">
          <Typography.Title level={4}>SQL</Typography.Title>
          <pre className="query-result__sql">
            <code>{result.sql}</code>
          </pre>
        </section>
      ) : null}

      {result?.chart ? (
        <section className="query-result__section">
          <Typography.Title level={4}>图表</Typography.Title>
          <Alert
            type="info"
            showIcon
            message="图表配置已返回"
            description="chart_builder 的最终格式确定后，可在这里接入 ECharts 渲染组件。"
          />
        </section>
      ) : null}

      <section className="query-result__section">
        <Typography.Title level={4}>数据表</Typography.Title>
        <ResultTable columns={result?.columns} rows={result?.rows} loading={loading} />
      </section>

      {result?.trace?.length ? (
        <>
          <Divider />
          <Collapse
            items={[
              {
                key: "trace",
                label: "Trace",
                children: (
                  <Space direction="vertical" size={6} className="query-result__trace">
                    {result.trace.map((item, index) => (
                      <Typography.Text code key={`${index}-${item}`}>
                        {item}
                      </Typography.Text>
                    ))}
                  </Space>
                ),
              },
            ]}
          />
        </>
      ) : null}
    </main>
  );
}
