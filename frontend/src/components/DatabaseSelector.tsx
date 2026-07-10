import { Select } from "antd";


interface Props{

 value:string;

 onChange:(value:string)=>void;

}


export default function DatabaseSelector({
 value,
 onChange

}:Props){


return (

<Select

style={{
width:200
}}

placeholder="选择数据库"

value={value}

onChange={onChange}


options={[

{
label:"MySQL",
value:"mysql"
},

{
label:"PostgreSQL",
value:"postgresql"
},

{
label:"Oracle",
value:"oracle"
}

]}

/>


)

}
