import { Link, Route, Routes } from "react-router-dom";
import { JobPage } from "./pages/JobPage";
import { UploadPage } from "./pages/UploadPage";

export default function App() {
  return (
    <div className="app">
      <header>
        <Link to="/">TT AI Coach</Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/jobs/:jobId" element={<JobPage />} />
        </Routes>
      </main>
    </div>
  );
}
