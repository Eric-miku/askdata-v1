import {
Timeline
} from "antd";


interface Props{

steps:string[];

}


export default function AgentTrace({
steps

}:Props){


return (

<Timeline

items={

steps.map(item=>({

children:item

}))

}

/>


)

}

