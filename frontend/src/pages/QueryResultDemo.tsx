import { Typography } from "antd";

import { QueryResultView } from "../components/QueryResultView";

import DatabaseSelector from "../components/DatabaseSelector";

import QueryInput from "../components/QueryInput";

import { useQueryStore } from "../store/queryStore";


export function QueryResultDemo() {


const {

database,

setDatabase,

loading,

result

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

}}

/>



<QueryResultView

result={result}

loading={loading}

/>



</div>

);

}
