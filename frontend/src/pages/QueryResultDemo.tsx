import { Typography } from "antd";
import { QueryResultView } from "../components/QueryResultView";
import type { QueryResponse } from "../types/query";

const demoResult: QueryResponse = {
  answer:
    "加州学校样例数据中，学生数量最高的学校是示例学校 A。这里使用 mock 数据展示表格分页和排序效果。",
  sql: "SELECT school_name, district, total_students, city FROM schools ORDER BY total_students DESC LIMIT 25;",
  columns: ["school_name", "district", "total_students", "city", "is_public"],
  rows: [
    ["示例学校 A", "洛杉矶联合学区", 5120, "Los Angeles", true],
    ["示例学校 B", "圣迭戈联合学区", 4388, "San Diego", true],
    ["示例学校 C", "旧金山联合学区", 3921, "San Francisco", true],
    ["示例学校 D", "萨克拉门托城市学区", 3186, "Sacramento", true],
    ["示例学校 E", "弗雷斯诺联合学区", 2870, "Fresno", true],
    ["示例学校 F", "奥克兰联合学区", 2654, "Oakland", true],
    ["示例学校 G", "长滩联合学区", 2499, "Long Beach", true],
    ["示例学校 H", "圣何塞联合学区", 2411, "San Jose", true],
    ["示例学校 I", "尔湾联合学区", 1980, "Irvine", true],
    ["示例学校 J", "帕萨迪纳联合学区", 1764, "Pasadena", true],
    ["示例学校 K", "伯克利联合学区", 1320, "Berkeley", true],
  ],
  chart: {
    type: "bar",
  },
  trace: [
    "[demo][+0.01s] 收到查询请求",
    "[demo][+0.15s] 生成 SQL",
    "[demo][+0.22s] 执行 SQL",
    "[demo][+0.31s] 返回结果",
  ],
};

export function QueryResultDemo() {
  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <Typography.Title level={2}>AskData 查询结果预览</Typography.Title>
      </header>
      <QueryResultView result={demoResult} />
    </div>
  );
}
