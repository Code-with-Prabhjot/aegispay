import Landing from "@/components/landing/Landing";
import Background from "@/components/landing/Background";

export default function HomePage() {
  return (
    <>
      <Background />
      <div className="page">
        <Landing />
      </div>
    </>
  );
}
