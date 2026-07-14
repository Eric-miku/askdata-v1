export type QueryCellValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[];

export interface TraceItem {

  step:string;

  status:string;

  message:string;

}



export interface QueryResponse {

  answer:string;

  sql:string;

  columns:string[];

  rows:Record<string, QueryCellValue>[];

  chart?:any;

  trace?:TraceItem[];

  error?:string;

}
