import NewJobForm from "@/components/NewJobForm";

const PIPELINE = [
  "probe",
  "transcribe",
  "analyze",
  "captions",
  "audio",
  "render",
  "export",
];

export default function Home() {
  return (
    <main id="main" className="container">
      <section className="hero">
        <p className="eyebrow">open source · self-hostable</p>
        <h1>HyproAI OSS</h1>
        <p className="lede">
          AI video editor for Indian languages: auto-cut dead air, Thanglish
          captions, export to Premiere, Resolve, and After Effects.
        </p>
        <div className="hero-actions">
          <a className="btn" href="#">
            View on GitHub
          </a>
          <p className="hero-note">runs locally · no account · MIT</p>
        </div>
      </section>

      <NewJobForm />

      <section className="how" aria-labelledby="how-title">
        <div className="section-head">
          <span className="section-num">04</span>
          <h2 className="section-title" id="how-title">
            What happens after you press start
          </h2>
          <span className="section-rule" aria-hidden="true" />
        </div>
        <p className="how-copy">
          One pass through the pipeline: the sidecar probes your file,
          transcribes word by word, decides what to cut, builds the captions,
          cleans the audio, renders the edit, and writes the NLE project. You
          watch each stage live, then download the video, the caption files, and
          the timeline.
        </p>
        <ol className="pipeline" aria-label="Pipeline stages">
          {PIPELINE.map((stage) => (
            <li key={stage}>{stage}</li>
          ))}
        </ol>
      </section>
    </main>
  );
}
