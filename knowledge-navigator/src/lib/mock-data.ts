export type KnowledgeFile = {
  id: string;
  name: string;
  source: "fireflies" | "manual";
  dateAdded: string;
  size: string;
  chunks: number;
  status: "processing" | "indexed" | "error";
  department?: string;
  category?: "Policy" | "Architecture" | "Q&A" | "Meeting" | "Other";
  preview?: string;
};

export type Meeting = {
  id: string;
  title: string;
  date: string;
  duration: string;
  participants: string[];
  markdownFile: string;
  summary: string;
};

export const mockFiles: KnowledgeFile[] = [
  {
    id: "f1",
    name: "Auth Sync - March 2026.md",
    source: "fireflies",
    dateAdded: "2026-03-14",
    size: "42 KB",
    chunks: 34,
    status: "indexed",
    department: "Engineering",
    category: "Meeting",
    preview:
      "# Auth Sync Notes\n\nKey decisions:\n- Migrate to WorkOS for SSO\n- Deprecate legacy JWT flow by Q3\n- Deployments handled via ArgoCD",
  },
  {
    id: "f2",
    name: "Platform Architecture Review.md",
    source: "fireflies",
    dateAdded: "2026-03-10",
    size: "28 KB",
    chunks: 22,
    status: "indexed",
    department: "Platform",
    category: "Architecture",
    preview: "# Architecture Review\n\nAdopted event-driven pattern via NATS.",
  },
  {
    id: "f3",
    name: "Onboarding Q&A - New Hires.md",
    source: "fireflies",
    dateAdded: "2026-03-08",
    size: "18 KB",
    chunks: 14,
    status: "indexed",
    department: "People",
    category: "Q&A",
    preview: "# Onboarding Q&A\n\nCommon questions from new engineers.",
  },
  {
    id: "f4",
    name: "Q1 Roadmap Planning.md",
    source: "fireflies",
    dateAdded: "2026-03-05",
    size: "52 KB",
    chunks: 41,
    status: "indexed",
    department: "Product",
    category: "Meeting",
    preview: "# Q1 Roadmap\n\nFocus on activation, retention, billing.",
  },
  {
    id: "f5",
    name: "Incident Postmortem - Feb 24.md",
    source: "fireflies",
    dateAdded: "2026-02-25",
    size: "31 KB",
    chunks: 26,
    status: "indexed",
    department: "SRE",
    category: "Meeting",
    preview: "# Postmortem\n\nRoot cause: cache stampede on auth service.",
  },
  {
    id: "f6",
    name: "Engineering Handbook.pdf",
    source: "manual",
    dateAdded: "2026-02-20",
    size: "1.2 MB",
    chunks: 187,
    status: "indexed",
    department: "Engineering",
    category: "Policy",
    preview: "# Engineering Handbook\n\nCoding standards, review process, on-call.",
  },
  {
    id: "f7",
    name: "Security Policy 2026.pdf",
    source: "manual",
    dateAdded: "2026-02-15",
    size: "820 KB",
    chunks: 96,
    status: "indexed",
    department: "Security",
    category: "Policy",
    preview: "# Security Policy\n\nSOC2, data retention, incident response.",
  },
  {
    id: "f8",
    name: "System Architecture Diagram.pdf",
    source: "manual",
    dateAdded: "2026-02-10",
    size: "2.4 MB",
    chunks: 42,
    status: "processing",
    department: "Platform",
    category: "Architecture",
    preview: "# System Architecture\n\nMicroservices topology overview.",
  },
];

export const mockMeetings: Meeting[] = [
  {
    id: "m1",
    title: "Auth Sync — March 2026",
    date: "2026-03-14",
    duration: "48 min",
    participants: ["Ana Chen", "Diego M.", "Priya S.", "Kai R."],
    markdownFile: "Auth Sync - March 2026.md",
    summary:
      "Migration to WorkOS finalized. Deprecation of legacy JWT flow slated for Q3. ArgoCD adopted for deployments.",
  },
  {
    id: "m2",
    title: "Platform Architecture Review",
    date: "2026-03-10",
    duration: "62 min",
    participants: ["Ana Chen", "Sam O.", "Rin T."],
    markdownFile: "Platform Architecture Review.md",
    summary: "Event-driven pattern via NATS approved. Removed legacy REST-to-REST fan-out.",
  },
  {
    id: "m3",
    title: "Onboarding Q&A — New Hires",
    date: "2026-03-08",
    duration: "35 min",
    participants: ["Priya S.", "3 new hires"],
    markdownFile: "Onboarding Q&A - New Hires.md",
    summary: "FAQ covering repo access, environments, staging URLs, on-call rotation.",
  },
  {
    id: "m4",
    title: "Q1 Roadmap Planning",
    date: "2026-03-05",
    duration: "90 min",
    participants: ["Leadership + Eng managers"],
    markdownFile: "Q1 Roadmap Planning.md",
    summary: "Three tracks: Activation, Retention, Billing. Owner assigned per track.",
  },
  {
    id: "m5",
    title: "Incident Postmortem — Feb 24",
    date: "2026-02-25",
    duration: "40 min",
    participants: ["SRE team", "Auth team"],
    markdownFile: "Incident Postmortem - Feb 24.md",
    summary: "Cache stampede on auth service; mitigated with request coalescing.",
  },
];

export const promptChips = [
  "Summarize key architectural decisions made in the last month.",
  "What is our team's deployment and staging process?",
  "Who are the key leads for Project Auth Rewrite?",
  "What are the on-call expectations for new engineers?",
];

export type Citation = { file: string; snippet: string };

export function mockAnswer(question: string): { text: string; citations: Citation[] } {
  const q = question.toLowerCase();
  if (q.includes("architect")) {
    return {
      text: "Recent architectural decisions center on adopting an event-driven pattern via NATS, replacing legacy REST fan-out. The platform team also standardized deployments on ArgoCD and is deprecating the legacy JWT auth flow by Q3.",
      citations: [
        {
          file: "Platform Architecture Review.md",
          snippet: "Adopted event-driven pattern via NATS. Removed legacy REST-to-REST fan-out.",
        },
        {
          file: "Auth Sync - March 2026.md",
          snippet: "Deprecate legacy JWT flow by Q3. Deployments handled via ArgoCD.",
        },
      ],
    };
  }
  if (q.includes("deploy") || q.includes("staging")) {
    return {
      text: "All services deploy through ArgoCD. Merging to `main` triggers a preview environment; a manual promote step ships to staging, and a signed release moves to production. Staging URLs follow `svc.staging.internal`.",
      citations: [
        {
          file: "Engineering Handbook.pdf",
          snippet: "Deploys flow: main → preview → staging (manual) → prod (signed release).",
        },
      ],
    };
  }
  if (q.includes("auth")) {
    return {
      text: "The Project Auth Rewrite is led by Ana Chen (tech lead) with Diego M. and Priya S. as core contributors. The team is migrating to WorkOS for SSO.",
      citations: [
        {
          file: "Auth Sync - March 2026.md",
          snippet: "Leads: Ana Chen (TL), Diego M., Priya S. Migrating to WorkOS.",
        },
      ],
    };
  }
  if (q.includes("on-call") || q.includes("oncall")) {
    return {
      text: "New engineers shadow on-call for two rotations before being paged directly. Rotations are weekly, Monday to Monday, with a primary and secondary responder.",
      citations: [
        {
          file: "Engineering Handbook.pdf",
          snippet: "Shadow 2 rotations, then primary/secondary weekly rotation.",
        },
      ],
    };
  }
  return {
    text: "Based on the ingested knowledge base, here is what I found. This response references the most relevant sources indexed from your Fireflies transcripts and uploaded documents.",
    citations: [
      {
        file: "Onboarding Q&A - New Hires.md",
        snippet: "General overview covering environments, repo access, and staging URLs.",
      },
    ],
  };
}
