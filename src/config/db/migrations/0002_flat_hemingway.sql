ALTER TABLE "cra_knowledge_document" ADD COLUMN "source_content" text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE "cra_knowledge_document" ADD COLUMN "source_filename" text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE "cra_knowledge_document" ADD COLUMN "mime_type" text DEFAULT 'text/markdown' NOT NULL;