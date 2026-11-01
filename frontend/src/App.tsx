import { useEffect, useState } from "react";
import Landing from "./Landing";
import Dashboard from "./Dashboard";

const route = () => (window.location.hash.startsWith("#/dashboard") ? "dashboard" : "landing");

export default function App() {
  const [page, setPage] = useState(route());
  useEffect(() => {
    const on = () => {
      setPage(route());
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return page === "dashboard" ? <Dashboard /> : <Landing />;
}
