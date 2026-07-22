import { useState } from "react";
import { useSessionStore } from "../store/sessionStore";


export default function HistorySidebar() {


    const [collapsed, setCollapsed] = useState(false);


    const {
        sessions,
        currentSessionId,
        switchSession

    } = useSessionStore();



    return (

        <aside

            className={`
                border-r
                h-screen
                transition-all
                duration-300
                bg-white

                ${collapsed ? "w-12" : "w-64"}

            `}

        >


            <button

                className="
                    p-2
                    border-b
                    w-full
                    cursor-pointer
                "

                onClick={() =>
                    setCollapsed(!collapsed)
                }

            >

                {
                    collapsed
                    ?
                    ">"
                    :
                    "<"
                }

            </button>



            {
                !collapsed && (

                    <div className="p-3">


                        <h2
                            className="
                            font-bold
                            mb-3
                            "
                        >

                            历史记录

                        </h2>



                        {
                            sessions.length === 0
                            ?

                            <div>
                                暂无历史记录
                            </div>

                            :

                            sessions.map(item => (


                                <div

                                    key={item.session_id}


                                    onClick={() => {


                                        console.log(
                                            "切换 session:",
                                            item.session_id
                                        );


                                        switchSession(
                                            item.session_id
                                        );


                                    }}


                                    className={`

                                        p-3
                                        mb-2
                                        rounded
                                        cursor-pointer
                                        hover:bg-gray-100


                                        ${
                                            currentSessionId === item.session_id
                                            ?
                                            "bg-gray-200"
                                            :
                                            ""
                                        }

                                    `}

                                >


                                    <div>

                                        {item.title}

                                    </div>


                                    <div
                                        className="
                                        text-sm
                                        text-gray-500
                                        "
                                    >

                                        {item.created_at}

                                    </div>


                                </div>


                            ))

                        }


                    </div>

                )
            }


        </aside>

    );

}