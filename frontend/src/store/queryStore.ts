import { create } from "zustand";
import type { QueryResponse } from "../types/query";


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

}



export const useQueryStore=create<QueryState>((set)=>({

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
    })


}));
