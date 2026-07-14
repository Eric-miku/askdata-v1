import { Typography } from "antd";
import { Alert } from "antd";

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

executeQuery,

error

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

setQuestion(question);

executeQuery(question);


}}

/>

{error ? (

<Alert

type="error"

showIcon

message="请求失败"

description={error}

/>

) : null}



<QueryResultView

result={result}

loading={loading}

/>



</div>

);

}
