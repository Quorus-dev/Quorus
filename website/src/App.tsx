import { Routes, Route } from "react-router-dom";
import { useEffect } from "react";
import CursorGlow from "./components/CursorGlow";
import AnnouncementBar from "./components/AnnouncementBar";
import Home from "./pages/Home";
import Console from "./pages/Console";

function ScrollReset() {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);
  return null;
}

export default function App() {
  return (
    <>
      <ScrollReset />
      <CursorGlow />
      <AnnouncementBar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/console" element={<Console />} />
      </Routes>
    </>
  );
}
