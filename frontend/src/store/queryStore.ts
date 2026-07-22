import { create } from "zustand";
import type { QueryResponse } from "../types/query";
import { queryData } from "../api/query";

interface QueryState {

  database:string;

  question:string;

  loading:boolean;

  error:string | null;

  trace:string[];

  result:QueryResponse | null;


  setDatabase:(db:string)=>void;

  setQuestion:(q:string)=>void;

  setLoading:(v:boolean)=>void;

  setError:(e:string|null)=>void;

  setTrace:(t:string[])=>void;

  setResult:(r:QueryResponse)=>void;

  executeQuery:()=>Promise<void>;

}

export interface QueryState {
  database: string;
  databases: DatabaseInfo[];
  databasesLoading: boolean;
  databaseError: string | null;
  sessionId: string | null;
  turns: ChatTurn[];
  loading: boolean;
  validationError: string | null;
  loadDatabases: () => Promise<void>;
  selectDatabase: (databaseId: string) => Promise<void>;
  newChat: () => Promise<void>;
  sendMessage: (question: string) => Promise<void>;
  retryTurn: (turnId: string) => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useQueryStore=create<QueryState>((set,get)=>({

  database:"",

  question:"",

  loading:false,

  error:null,

  trace:[],

  result:null,


  setDatabase:(db)=>
    set({
      database:db
    }),


  setQuestion:(q)=>
    set({
      question:q
    }),


  setLoading:(v)=>
    set({
      loading:v
    }),


  setError:(e)=>
    set({
      error:e
    }),


  setTrace:(t)=>
    set({
      trace:t
    }),


  setResult:(r)=>
    set({
      result:r
    }),

executeQuery:async()=>{

  const {
    database,
    question
  }=get();


  try{

    set({
      loading:true,
      error:null
    });


    const result=await queryData({

      database_id:database,

      question

    });


    set({

      result,

      loading:false

    });


  }catch(error){

    set({

      error:String(error),

      loading:false

    });

  }

}

export const useQueryStore = createQueryStore();
