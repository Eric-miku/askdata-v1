import { QueryResultDemo } from "./pages/QueryResultDemo";
import HistorySidebar from "./components/HistorySidebar";


export default function App() {

  return (

    <div className="flex h-screen">


      <HistorySidebar />


      <main className="flex-1">


        <QueryResultDemo />


      </main>


    </div>

  );

}