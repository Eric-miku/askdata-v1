import {
Input,
Button
} from "antd";

import {
useState
} from "react";


interface Props{

loading:boolean;

onSubmit:(text:string)=>void;

}



export default function QueryInput({
loading,
onSubmit

}:Props){


const [value,setValue]=useState("");



return (

<>

<Input.TextArea

rows={4}

placeholder="请输入你的问题，例如：查询销售额最高的产品"

value={value}

onChange={
e=>setValue(e.target.value)
}

/>


<Button

type="primary"

loading={loading}

onClick={()=>onSubmit(value)}

>

开始分析

</Button>


</>

)

}
