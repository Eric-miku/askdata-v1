import { Typography } from "antd";

import { QueryResultView } from "../components/QueryResultView";

import DatabaseSelector from "../components/DatabaseSelector";

import QueryInput from "../components/QueryInput";

import { useQueryStore } from "../store/queryStore";


export function QueryResultDemo() {


const {

database,

setDatabase,

setQuestion,

loading,

result,

executeQuery

}=useQueryStore();



return (

<div className="app-shell">


<header className="app-shell__header">


<Typography.Title level={2}>

AskData 智能问数

</Typography.Title>


</header>



<DatabaseSelector

value={database}

onChange={setDatabase}

/>



<QueryInput

loading={loading}

onSubmit={(question)=>{

console.log(question);

executeQuery();


}}

/>



<QueryResultView

result={result}

loading={loading}

/>



</div>

);

}
