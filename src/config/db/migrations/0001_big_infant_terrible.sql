CREATE TABLE "cra_agent_event" (
	"id" serial PRIMARY KEY NOT NULL,
	"case_id" text NOT NULL,
	"event_type" text NOT NULL,
	"title" text NOT NULL,
	"detail" text NOT NULL,
	"sources" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_bad_case" (
	"id" serial PRIMARY KEY NOT NULL,
	"case_id" text NOT NULL,
	"node" text NOT NULL,
	"failure_type" text NOT NULL,
	"exposure" text NOT NULL,
	"impact" text NOT NULL,
	"priority" text NOT NULL,
	"detection_source" text NOT NULL,
	"response" text NOT NULL,
	"status" text DEFAULT 'open' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_case" (
	"case_id" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"bp" text NOT NULL,
	"credit_segment" text NOT NULL,
	"requested_cl_usd" double precision DEFAULT 0 NOT NULL,
	"statement_currency" text NOT NULL,
	"customer" jsonb NOT NULL,
	"expected_entity_id" text DEFAULT '' NOT NULL,
	"business" text DEFAULT '' NOT NULL,
	"new_year" jsonb NOT NULL,
	"inputs" jsonb NOT NULL,
	"mock" boolean DEFAULT true NOT NULL,
	"package" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_case_note" (
	"case_id" text PRIMARY KEY NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_data_migration" (
	"id" text PRIMARY KEY NOT NULL,
	"completed_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_human_task" (
	"id" serial PRIMARY KEY NOT NULL,
	"case_id" text NOT NULL,
	"task_type" text NOT NULL,
	"title" text NOT NULL,
	"description" text NOT NULL,
	"status" text DEFAULT 'open' NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"resolved_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "cra_knowledge_chunk" (
	"id" text PRIMARY KEY NOT NULL,
	"document_id" text NOT NULL,
	"citation" text NOT NULL,
	"section" text DEFAULT '' NOT NULL,
	"page" integer,
	"content" text NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_knowledge_document" (
	"id" text PRIMARY KEY NOT NULL,
	"library_id" text NOT NULL,
	"title" text NOT NULL,
	"citation" text NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_stage_snapshot" (
	"id" serial PRIMARY KEY NOT NULL,
	"case_id" text NOT NULL,
	"stage" text NOT NULL,
	"stage_index" integer NOT NULL,
	"title" text NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"completed_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cra_workflow_state" (
	"case_id" text PRIMARY KEY NOT NULL,
	"status" text NOT NULL,
	"current_stage" text NOT NULL,
	"stage_index" integer NOT NULL,
	"current_task" text NOT NULL,
	"blocker" text DEFAULT '' NOT NULL,
	"completed_tasks" integer DEFAULT 0 NOT NULL,
	"total_tasks" integer DEFAULT 0 NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "cra_agent_event" ADD CONSTRAINT "cra_agent_event_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_bad_case" ADD CONSTRAINT "cra_bad_case_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_case_note" ADD CONSTRAINT "cra_case_note_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_human_task" ADD CONSTRAINT "cra_human_task_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_knowledge_chunk" ADD CONSTRAINT "cra_knowledge_chunk_document_id_cra_knowledge_document_id_fk" FOREIGN KEY ("document_id") REFERENCES "public"."cra_knowledge_document"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_stage_snapshot" ADD CONSTRAINT "cra_stage_snapshot_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cra_workflow_state" ADD CONSTRAINT "cra_workflow_state_case_id_cra_case_case_id_fk" FOREIGN KEY ("case_id") REFERENCES "public"."cra_case"("case_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_cra_agent_event_case" ON "cra_agent_event" USING btree ("case_id","id");--> statement-breakpoint
CREATE INDEX "idx_cra_bad_case_case_status" ON "cra_bad_case" USING btree ("case_id","status");--> statement-breakpoint
CREATE INDEX "idx_cra_case_bp" ON "cra_case" USING btree ("bp");--> statement-breakpoint
CREATE INDEX "idx_cra_case_updated_at" ON "cra_case" USING btree ("updated_at");--> statement-breakpoint
CREATE INDEX "idx_cra_human_task_case_status" ON "cra_human_task" USING btree ("case_id","status");--> statement-breakpoint
CREATE INDEX "idx_cra_knowledge_chunk_document" ON "cra_knowledge_chunk" USING btree ("document_id");--> statement-breakpoint
CREATE INDEX "idx_cra_knowledge_document_library" ON "cra_knowledge_document" USING btree ("library_id");--> statement-breakpoint
CREATE INDEX "idx_cra_stage_snapshot_case_stage" ON "cra_stage_snapshot" USING btree ("case_id","stage");