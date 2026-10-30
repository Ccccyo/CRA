CREATE TABLE IF NOT EXISTS "cra_file_object" (
	"id" text PRIMARY KEY NOT NULL,
	"purpose" text NOT NULL,
	"storage_provider" text NOT NULL,
	"bucket" text DEFAULT '' NOT NULL,
	"object_key" text NOT NULL,
	"original_filename" text NOT NULL,
	"mime_declared" text NOT NULL,
	"mime_detected" text DEFAULT '' NOT NULL,
	"size_bytes" integer NOT NULL,
	"sha256" text DEFAULT '' NOT NULL,
	"etag" text DEFAULT '' NOT NULL,
	"status" text DEFAULT 'INITIATED' NOT NULL,
	"version" integer DEFAULT 1 NOT NULL,
	"parent_file_id" text,
	"uploaded_by" text NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ready_at" timestamp with time zone,
	"deleted_at" timestamp with time zone,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "idx_cra_file_object_key" ON "cra_file_object" USING btree ("object_key");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_file_owner_status" ON "cra_file_object" USING btree ("uploaded_by","status");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_file_parent" ON "cra_file_object" USING btree ("parent_file_id");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "cra_upload_session" (
	"id" text PRIMARY KEY NOT NULL,
	"file_id" text NOT NULL,
	"idempotency_key" text NOT NULL,
	"expected_size_bytes" integer NOT NULL,
	"expected_mime" text NOT NULL,
	"expected_sha256" text DEFAULT '' NOT NULL,
	"status" text DEFAULT 'OPEN' NOT NULL,
	"created_by" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"completed_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "cra_upload_session_file_id_cra_file_object_id_fk" FOREIGN KEY ("file_id") REFERENCES "cra_file_object"("id") ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "idx_cra_upload_idempotency" ON "cra_upload_session" USING btree ("created_by","idempotency_key");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_upload_file" ON "cra_upload_session" USING btree ("file_id");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "cra_file_binding" (
	"id" serial PRIMARY KEY NOT NULL,
	"file_id" text NOT NULL,
	"entity_type" text NOT NULL,
	"entity_id" text NOT NULL,
	"role" text NOT NULL,
	"version" integer DEFAULT 1 NOT NULL,
	"is_current" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "cra_file_binding_file_id_cra_file_object_id_fk" FOREIGN KEY ("file_id") REFERENCES "cra_file_object"("id") ON DELETE restrict
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "idx_cra_file_binding_version" ON "cra_file_binding" USING btree ("entity_type","entity_id","role","version");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_file_binding_current" ON "cra_file_binding" USING btree ("entity_type","entity_id","role","is_current");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_file_binding_file" ON "cra_file_binding" USING btree ("file_id");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "cra_file_audit_log" (
	"id" serial PRIMARY KEY NOT NULL,
	"file_id" text NOT NULL,
	"actor_user_id" text NOT NULL,
	"action" text NOT NULL,
	"result" text NOT NULL,
	"request_id" text DEFAULT '' NOT NULL,
	"detail" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_cra_file_audit_file" ON "cra_file_audit_log" USING btree ("file_id","id");
