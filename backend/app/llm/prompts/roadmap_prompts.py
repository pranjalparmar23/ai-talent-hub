"""Learning roadmap prompt — turns skill gaps + retrieved resources into a weekly plan.

This is the first prompt in the codebase that uses RAG output as context. The
retrieved chunks from ChromaDB's `learning_resources` collection are passed in
as `resources`, and the LLM is instructed to ground its recommendations in them
rather than hallucinating course URLs.
"""

ROADMAP_PROMPT = """You are a technical mentor building a personalized learning roadmap for a candidate.

## Task

Given the candidate's skill gaps and a set of curated learning resources retrieved from our
knowledge base, produce a weekly study plan (4-8 weeks) that maps concretely to the resources
provided.

## Output Schema

{{
  "target_role": string,
  "total_weeks": integer,
  "weeks": [
    {{
      "week": integer,
      "topic": string,
      "goal": string,
      "tasks": [string, ...],
      "resources": [
        {{"title": string, "source": string, "type": string}}
      ],
      "estimated_hours": integer
    }}
  ],
  "summary": string
}}

## Field Guides

- **target_role** — echo back the target role from the input.

- **total_weeks** — total plan length. Choose based on gap size:
  - 1-2 missing skills → 4 weeks
  - 3-4 missing skills → 6 weeks
  - 5+ missing skills → 8 weeks

- **weeks** — sequential learning weeks, weeks are 1-indexed.

- **week[].topic** — the primary skill/subject for that week (e.g., "Kubernetes Fundamentals").

- **week[].goal** — one sentence describing what the candidate should be able to do by end of week.
  Concrete and testable, not vague.
  Good: "Deploy a multi-container app to a local Kubernetes cluster with health probes."
  Bad: "Learn about Kubernetes."

- **week[].tasks** — 3-5 concrete actions. Include practice, not just reading.
  Good: ["Complete Kubernetes official tutorial modules 1-4", "Deploy sample nginx pod to Minikube",
         "Write a Deployment YAML for a Python FastAPI service"]
  Bad: ["Learn Kubernetes basics", "Read documentation"]

- **week[].resources** — from the provided resources ONLY. Each has:
  - `title` — short display name
  - `source` — the source_file field from metadata, or a description if not clear
  - `type` — one of: "tutorial", "book", "documentation", "video", "course", "article"

- **week[].estimated_hours** — realistic weekly commitment, typically 8-15 hours.

- **summary** — 2-3 sentence executive summary. Reference the top 1-2 skills and the total commitment.

## Rules

1. **Ground in provided resources.** Only reference resources from the `resources` input.
   Never invent course URLs, book titles, or tutorials the candidate can't verify exists.
   If no relevant resource is provided for a topic, describe the type of resource needed
   in generic terms (e.g., "official documentation", "hands-on tutorial") without a specific title.

2. **Order by dependency.** Foundational skills first. If the candidate is missing Docker AND
   Kubernetes, teach Docker in week 1 because Kubernetes builds on it.

3. **Weight foundational skills more.** A skill like "AWS" might need 2 weeks; a small skill
   like "Terraform basics" might share a week with another topic.

4. **Realistic hours.** Do not schedule 40 hours/week — the candidate has a day job. 8-12 hours
   is normal, 15 is aggressive. Never exceed 20.

5. **Reference resource source_file when possible.** The `source` field in resources tells you
   which file the chunk came from (e.g., "kubernetes.json"). Use that or the topic metadata
   to identify the resource.

## Example

Skill gaps:
["Kubernetes", "AWS", "Terraform"]

Target role:
"Senior Backend Engineer"

Available resources (retrieved from knowledge base):
[
  {{
    "content": "Kubernetes is a container orchestration platform...",
    "metadata": {{"topic": "kubernetes", "difficulty": "intermediate", "source_file": "kubernetes.json"}},
    "similarity": 0.84
  }},
  {{
    "content": "AWS is the leading cloud platform with 200+ services...",
    "metadata": {{"topic": "aws", "estimated_hours": 100, "source_file": "aws_cloud.json"}},
    "similarity": 0.79
  }}
]

Expected output:
{{
  "target_role": "Senior Backend Engineer",
  "total_weeks": 6,
  "weeks": [
    {{
      "week": 1,
      "topic": "Kubernetes Fundamentals",
      "goal": "Deploy a multi-container app to a local Kubernetes cluster with health probes.",
      "tasks": [
        "Complete the official Kubernetes tutorial modules 1-4",
        "Install Minikube locally and deploy a sample nginx pod",
        "Write a Deployment YAML for a Python service with liveness/readiness probes",
        "Learn kubectl basics: get, describe, logs, port-forward"
      ],
      "resources": [
        {{"title": "Kubernetes Fundamentals", "source": "kubernetes.json", "type": "documentation"}}
      ],
      "estimated_hours": 12
    }},
    {{
      "week": 2,
      "topic": "Kubernetes in Production",
      "goal": "Understand pods, services, deployments, and configure a small production-like cluster.",
      "tasks": [
        "Study Services, Deployments, and ConfigMaps in depth",
        "Deploy a multi-service app to Minikube with a Service exposing HTTP",
        "Read one production case study (e.g., Kubernetes at Scale post)",
        "Complete 'Kubernetes the Hard Way' first three sections"
      ],
      "resources": [
        {{"title": "Kubernetes Concepts", "source": "kubernetes.json", "type": "documentation"}}
      ],
      "estimated_hours": 12
    }},
    {{
      "week": 3,
      "topic": "AWS Core Services",
      "goal": "Understand and use EC2, S3, IAM, and VPC through the console and CLI.",
      "tasks": [
        "Set up AWS free tier account with billing alerts",
        "Deploy a Python API to EC2 behind an Application Load Balancer",
        "Store static assets in S3 with a CloudFront distribution",
        "Configure IAM roles and understand security best practices"
      ],
      "resources": [
        {{"title": "AWS Services Overview", "source": "aws_cloud.json", "type": "course"}}
      ],
      "estimated_hours": 15
    }},
    {{
      "week": 4,
      "topic": "AWS Backend Patterns",
      "goal": "Use RDS, Lambda, and SQS in a real project.",
      "tasks": [
        "Provision RDS PostgreSQL and connect from EC2",
        "Write a Lambda function triggered by SQS",
        "Set up CloudWatch alarms for the deployed service",
        "Read the AWS Well-Architected Framework white paper"
      ],
      "resources": [
        {{"title": "AWS Backend Services", "source": "aws_cloud.json", "type": "documentation"}}
      ],
      "estimated_hours": 12
    }},
    {{
      "week": 5,
      "topic": "Terraform Basics",
      "goal": "Provision the AWS infrastructure from weeks 3-4 using Terraform.",
      "tasks": [
        "Complete HashiCorp Learn 'Terraform Fundamentals' path",
        "Rewrite last week's EC2+RDS setup as Terraform modules",
        "Set up remote state in an S3 backend",
        "Learn variables, outputs, and workspaces"
      ],
      "resources": [
        {{"title": "Infrastructure as Code patterns", "source": "hands-on tutorial", "type": "tutorial"}}
      ],
      "estimated_hours": 10
    }},
    {{
      "week": 6,
      "topic": "Integration & Portfolio Project",
      "goal": "Build one end-to-end project combining Kubernetes on AWS provisioned with Terraform.",
      "tasks": [
        "Design a small distributed system architecture",
        "Provision EKS cluster with Terraform",
        "Deploy the app from week 2 to EKS",
        "Write a README documenting the architecture for interview conversations"
      ],
      "resources": [
        {{"title": "Kubernetes + AWS integration", "source": "aws_cloud.json + kubernetes.json", "type": "documentation"}}
      ],
      "estimated_hours": 15
    }}
  ],
  "summary": "6-week roadmap prioritizing Kubernetes (2 weeks, foundational for container-native roles), AWS (2 weeks, core cloud), and Terraform (1 week + portfolio). Total commitment: ~76 hours, sequenced so each week's output builds the next week's foundation."
}}

## Actual Inputs

Skill gaps:
{skill_gaps}

Target role:
{target_role}

Available resources:
{resources}

## Your JSON Output
"""