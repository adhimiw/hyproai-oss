import JobClient from "@/components/JobClient";

interface JobPageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: JobPageProps) {
  const { id } = await params;
  return { title: `Job ${id.slice(0, 8)}` };
}

export default async function JobPage({ params }: JobPageProps) {
  const { id } = await params;
  return <JobClient id={id} />;
}
