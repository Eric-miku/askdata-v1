import axios from "axios";
import type { QueryResponse } from "../types/query";


interface QueryRequest {
  database_id:string;
  question:string;
}



export async function queryData(

  data:QueryRequest

):Promise<QueryResponse>{


const response = await axios.post(

  "http://7.59.11.153:8003/api/query",

  data

);


return response.data;

}
