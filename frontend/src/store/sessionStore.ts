import { create } from "zustand";


export interface SessionItem {

    session_id:string;

    title:string;

    created_at:string;

}


interface SessionState {

    currentSessionId:string | null;

    sessions:SessionItem[];

    setSessions:
    (sessions:SessionItem[])=>void;

    switchSession:
    (sessionId:string)=>void;

}


export const useSessionStore=create<SessionState>((set)=>({

    currentSessionId:null,

   sessions:[

    {
        session_id:"session_001",
        title:"销售额分析",
        created_at:"2026-07-21"
    },


    {
        session_id:"session_002",
        title:"用户增长分析",
        created_at:"2026-07-20"
    },


    {
        session_id:"session_003",
        title:"订单统计分析",
        created_at:"2026-07-19"
    }

],


    setSessions:(sessions)=>
        set({
            sessions
        }),


    switchSession:(sessionId)=>
        set({
            currentSessionId:sessionId
        })


}));