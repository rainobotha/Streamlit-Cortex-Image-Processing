-- Building Inspection Image Analyzer - Database Setup Script
-- This script creates all necessary database objects for the Streamlit application
-- 
-- The application uses SNOWFLAKE.CORTEX.COMPLETE with multimodal models for AI analysis,
-- providing flexible and powerful image analysis capabilities

-- Create database for building inspection system
CREATE DATABASE IF NOT EXISTS BUILDING_INSPECTION_DB
  COMMENT = 'Database for building inspection image analysis system';

-- Use the database
USE DATABASE BUILDING_INSPECTION_DB;

-- Create schema for inspection data
CREATE SCHEMA IF NOT EXISTS INSPECTION_SCHEMA
  COMMENT = 'Schema containing building inspection images and analysis results';

-- Use the schema
USE SCHEMA INSPECTION_SCHEMA;

-- Create stage for storing building inspection images
CREATE OR REPLACE STAGE BUILDING_INSPECTION_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Stage for storing building inspection images';

-- Create staging table for file metadata (alternative to PUT commands in SiS)
-- Binary data is stored in chunked format for persistence across app restarts
CREATE OR REPLACE TABLE stage_file_data (
    file_id STRING PRIMARY KEY,
    filename STRING NOT NULL,
    upload_time TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    file_size NUMBER NOT NULL,
    file_type STRING,
    storage_type STRING DEFAULT 'CHUNKED_DB',
    uploader STRING DEFAULT CURRENT_USER(),
    status STRING DEFAULT 'ACTIVE'
);

-- Create table for storing file chunks (binary data split into manageable pieces)
CREATE OR REPLACE TABLE stage_file_chunks (
    chunk_id STRING PRIMARY KEY,
    file_id STRING NOT NULL,
    chunk_index NUMBER NOT NULL,
    chunk_data VARCHAR(16000000),
    chunk_size NUMBER NOT NULL,
    upload_time TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    FOREIGN KEY (file_id) REFERENCES stage_file_data(file_id)
);

-- Create table to track uploaded images
CREATE OR REPLACE TABLE image_uploads (
    upload_id STRING PRIMARY KEY,
    filename STRING NOT NULL,
    original_name STRING NOT NULL,
    file_size NUMBER NOT NULL,
    upload_time TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    stage_path STRING NOT NULL,
    file_type STRING,
    image_dimensions STRING,
    uploader STRING DEFAULT CURRENT_USER(),
    status STRING DEFAULT 'UPLOADED',
    metadata VARIANT
);

-- Create table to store analysis results
CREATE OR REPLACE TABLE analysis_results (
    analysis_id STRING PRIMARY KEY,
    upload_id STRING NOT NULL,
    filename STRING NOT NULL,
    analysis_prompt TEXT NOT NULL,
    analysis_result TEXT,
    confidence_score FLOAT,
    detected_issues ARRAY,
    recommendations ARRAY,
    analysis_time TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    analyzer STRING DEFAULT CURRENT_USER(),
    processing_time_ms NUMBER,
    model_used STRING,
    metadata VARIANT,
    FOREIGN KEY (upload_id) REFERENCES image_uploads(upload_id)
);

-- Create table for inspection reports
CREATE OR REPLACE TABLE inspection_reports (
    report_id STRING PRIMARY KEY,
    report_name STRING NOT NULL,
    report_date DATE DEFAULT CURRENT_DATE(),
    inspector STRING DEFAULT CURRENT_USER(),
    building_address STRING,
    building_type STRING,
    inspection_type STRING,
    overall_status STRING,
    priority_level STRING,
    total_images NUMBER,
    total_issues NUMBER,
    avg_confidence_score FLOAT,
    report_summary TEXT,
    created_time TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    metadata VARIANT
);

-- Create table to link images to reports
CREATE OR REPLACE TABLE report_image_links (
    link_id STRING PRIMARY KEY,
    report_id STRING NOT NULL,
    upload_id STRING NOT NULL,
    analysis_id STRING,
    image_order NUMBER,
    include_in_summary BOOLEAN DEFAULT TRUE,
    notes TEXT,
    FOREIGN KEY (report_id) REFERENCES inspection_reports(report_id),
    FOREIGN KEY (upload_id) REFERENCES image_uploads(upload_id),
    FOREIGN KEY (analysis_id) REFERENCES analysis_results(analysis_id)
);

-- Create view for comprehensive analysis results
CREATE OR REPLACE VIEW v_analysis_summary AS
SELECT 
    ar.analysis_id,
    ar.filename,
    iu.original_name,
    ar.analysis_prompt,
    ar.confidence_score,
    ar.detected_issues,
    ar.recommendations,
    ar.analysis_time,
    ar.analyzer,
    iu.upload_time,
    iu.file_size,
    iu.file_type,
    iu.stage_path,
    CASE 
        WHEN ar.confidence_score >= 0.9 THEN 'HIGH'
        WHEN ar.confidence_score >= 0.7 THEN 'MEDIUM'
        ELSE 'LOW'
    END AS confidence_level,
    ARRAY_SIZE(ar.detected_issues) as issue_count,
    ARRAY_SIZE(ar.recommendations) as recommendation_count
FROM analysis_results ar
JOIN image_uploads iu ON ar.upload_id = iu.upload_id
ORDER BY ar.analysis_time DESC;

-- Create view for image uploads with file metadata and chunk information
CREATE OR REPLACE VIEW v_image_uploads_with_data AS
SELECT 
    iu.upload_id,
    iu.filename,
    iu.original_name,
    iu.file_size,
    iu.upload_time,
    iu.stage_path,
    iu.file_type,
    iu.uploader,
    iu.status,
    sfd.file_id,
    sfd.storage_type,
    sfd.upload_time AS staging_upload_time,
    COUNT(sfc.chunk_id) AS chunk_count,
    SUM(sfc.chunk_size) AS total_chunk_size,
    CASE 
        WHEN sfd.file_id IS NOT NULL THEN sfd.storage_type
        WHEN iu.stage_path LIKE '@%' THEN 'STAGE'
        ELSE 'MEMORY'
    END AS effective_storage_type,
    CASE 
        WHEN sfd.file_id IS NOT NULL THEN TRUE
        ELSE FALSE
    END AS has_metadata,
    CASE 
        WHEN sfd.storage_type = 'CHUNKED_DB' AND COUNT(sfc.chunk_id) > 0 THEN TRUE
        ELSE FALSE
    END AS has_binary_data
FROM image_uploads iu
LEFT JOIN stage_file_data sfd ON iu.filename = sfd.filename AND sfd.status = 'ACTIVE'
LEFT JOIN stage_file_chunks sfc ON sfd.file_id = sfc.file_id
GROUP BY iu.upload_id, iu.filename, iu.original_name, iu.file_size, iu.upload_time, 
         iu.stage_path, iu.file_type, iu.uploader, iu.status, sfd.file_id, 
         sfd.storage_type, sfd.upload_time
ORDER BY iu.upload_time DESC;

-- Create view for inspection dashboard metrics
CREATE OR REPLACE VIEW v_inspection_metrics AS
SELECT 
    COUNT(DISTINCT iu.upload_id) as total_images,
    COUNT(DISTINCT ar.analysis_id) as total_analyses,
    AVG(ar.confidence_score) as avg_confidence,
    COUNT(DISTINCT ar.analyzer) as unique_analyzers,
    COUNT(DISTINCT DATE(iu.upload_time)) as active_days,
    SUM(iu.file_size) as total_storage_bytes,
    SUM(ARRAY_SIZE(ar.detected_issues)) as total_issues,
    SUM(ARRAY_SIZE(ar.recommendations)) as total_recommendations
FROM image_uploads iu
LEFT JOIN analysis_results ar ON iu.upload_id = ar.upload_id
WHERE iu.upload_time >= CURRENT_DATE() - INTERVAL '30 days';

-- Create stored procedure for image upload logging
CREATE OR REPLACE PROCEDURE log_image_upload(
    filename STRING,
    original_name STRING,
    file_size NUMBER,
    stage_path STRING,
    file_type STRING DEFAULT NULL,
    image_dimensions STRING DEFAULT NULL,
    metadata VARIANT DEFAULT NULL
)
RETURNS STRING
LANGUAGE SQL
AS
$$
DECLARE
    upload_id STRING;
BEGIN
    -- Generate unique upload ID
    upload_id := CONCAT('IMG_', TO_CHAR(CURRENT_TIMESTAMP(), 'YYYYMMDD_HH24MISS_'), RANDSTR(8, RANDOM()));
    
    -- Insert upload record
    INSERT INTO image_uploads (
        upload_id, filename, original_name, file_size, stage_path, 
        file_type, image_dimensions, metadata
    ) VALUES (
        upload_id, filename, original_name, file_size, stage_path,
        file_type, image_dimensions, metadata
    );
    
    RETURN upload_id;
END;
$$;

-- Create stored procedure for logging analysis results
CREATE OR REPLACE PROCEDURE log_analysis_result(
    upload_id STRING,
    filename STRING,
    analysis_prompt TEXT,
    analysis_result TEXT,
    confidence_score FLOAT DEFAULT NULL,
    detected_issues ARRAY DEFAULT NULL,
    recommendations ARRAY DEFAULT NULL,
    processing_time_ms NUMBER DEFAULT NULL,
    model_used STRING DEFAULT 'SNOWFLAKE.CORTEX.COMPLETE (claude-3-7-sonnet)',
    metadata VARIANT DEFAULT NULL
)
RETURNS STRING
LANGUAGE SQL
AS
$$
DECLARE
    analysis_id STRING;
BEGIN
    -- Generate unique analysis ID
    analysis_id := CONCAT('ANA_', TO_CHAR(CURRENT_TIMESTAMP(), 'YYYYMMDD_HH24MISS_'), RANDSTR(8, RANDOM()));
    
    -- Insert analysis record
    INSERT INTO analysis_results (
        analysis_id, upload_id, filename, analysis_prompt, analysis_result,
        confidence_score, detected_issues, recommendations, processing_time_ms,
        model_used, metadata
    ) VALUES (
        analysis_id, upload_id, filename, analysis_prompt, analysis_result,
        confidence_score, detected_issues, recommendations, processing_time_ms,
        model_used, metadata
    );
    
    RETURN analysis_id;
END;
$$;

-- Note: Custom analysis functions removed as the application now uses SNOWFLAKE.CORTEX.COMPLETE directly
-- This provides better flexibility and avoids dependency on custom functions

-- Create function to calculate risk score based on detected issues
CREATE OR REPLACE FUNCTION calculate_risk_score(detected_issues ARRAY)
RETURNS NUMBER(2,1)
LANGUAGE SQL
AS
$$
SELECT 
    CASE 
        WHEN ARRAY_SIZE(detected_issues) = 0 THEN 0.0
        WHEN ARRAY_SIZE(detected_issues) BETWEEN 1 AND 2 THEN 0.3
        WHEN ARRAY_SIZE(detected_issues) BETWEEN 3 AND 5 THEN 0.6
        WHEN ARRAY_SIZE(detected_issues) BETWEEN 6 AND 8 THEN 0.8
        ELSE 1.0
    END
$$;

-- Create task to clean up old temporary files (runs daily)
CREATE OR REPLACE TASK cleanup_old_uploads
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = 'USING CRON 0 2 * * * UTC'  -- Run daily at 2 AM UTC
AS
  -- Remove upload records older than 90 days
  DELETE FROM image_uploads 
  WHERE upload_time < CURRENT_TIMESTAMP() - INTERVAL '90 days'
  AND status = 'UPLOADED';

-- Create task to clean up old staging table files (runs daily)
CREATE OR REPLACE TASK cleanup_old_stage_files
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = 'USING CRON 0 3 * * * UTC'  -- Run daily at 3 AM UTC
AS
  -- Remove staging file chunks older than 90 days
  DELETE FROM stage_file_chunks 
  WHERE upload_time < CURRENT_TIMESTAMP() - INTERVAL '90 days';
  
  -- Remove staging file data older than 90 days
  DELETE FROM stage_file_data 
  WHERE upload_time < CURRENT_TIMESTAMP() - INTERVAL '90 days'
  AND status = 'ACTIVE';

-- Create task to generate daily inspection metrics
CREATE OR REPLACE TASK daily_inspection_metrics
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = 'USING CRON 0 1 * * * UTC'  -- Run daily at 1 AM UTC
AS
  -- Create daily metrics summary
  CREATE OR REPLACE TEMPORARY TABLE daily_metrics AS
  SELECT 
    CURRENT_DATE() as report_date,
    COUNT(DISTINCT iu.upload_id) as images_uploaded,
    COUNT(DISTINCT ar.analysis_id) as analyses_completed,
    AVG(ar.confidence_score) as avg_confidence,
    SUM(ARRAY_SIZE(ar.detected_issues)) as total_issues_detected,
    COUNT(DISTINCT ar.analyzer) as active_users
  FROM image_uploads iu
  LEFT JOIN analysis_results ar ON iu.upload_id = ar.upload_id
  WHERE DATE(iu.upload_time) = CURRENT_DATE() - 1;

-- Grant permissions for Streamlit application
GRANT USAGE ON DATABASE BUILDING_INSPECTION_DB TO ROLE SYSADMIN;
GRANT USAGE ON SCHEMA INSPECTION_SCHEMA TO ROLE SYSADMIN;
GRANT ALL ON STAGE BUILDING_INSPECTION_STAGE TO ROLE SYSADMIN;
GRANT ALL ON ALL TABLES IN SCHEMA INSPECTION_SCHEMA TO ROLE SYSADMIN;
GRANT ALL ON ALL VIEWS IN SCHEMA INSPECTION_SCHEMA TO ROLE SYSADMIN;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA INSPECTION_SCHEMA TO ROLE SYSADMIN;
GRANT ALL ON ALL PROCEDURES IN SCHEMA INSPECTION_SCHEMA TO ROLE SYSADMIN;

-- Create sample data for testing (optional)
INSERT INTO inspection_reports (
    report_id, report_name, building_address, building_type, inspection_type,
    overall_status, priority_level, total_images, total_issues, avg_confidence_score,
    report_summary
) VALUES (
    'RPT_SAMPLE_001',
    'Sample Building Inspection Report',
    '123 Main Street, Anytown, USA',
    'Commercial Office Building',
    'Annual Safety Inspection',
    'NEEDS_ATTENTION',
    'MEDIUM',
    0,
    0,
    0.0,
    'Sample inspection report for testing purposes'
);

-- Display setup completion message
SELECT 'Building Inspection Database Setup Complete!' as STATUS,
       'Database: BUILDING_INSPECTION_DB' as DATABASE_NAME,
       'Schema: INSPECTION_SCHEMA' as SCHEMA_NAME,
       'Stage: BUILDING_INSPECTION_STAGE' as STAGE_NAME,
       'Tables Created: 4' as TABLES_COUNT,
       'Views Created: 2' as VIEWS_COUNT,
       'Functions Created: 3' as FUNCTIONS_COUNT,
       'Procedures Created: 2' as PROCEDURES_COUNT;

-- Show created objects
SHOW TABLES IN SCHEMA BUILDING_INSPECTION_DB.INSPECTION_SCHEMA;
SHOW VIEWS IN SCHEMA BUILDING_INSPECTION_DB.INSPECTION_SCHEMA;
SHOW FUNCTIONS IN SCHEMA BUILDING_INSPECTION_DB.INSPECTION_SCHEMA;
SHOW PROCEDURES IN SCHEMA BUILDING_INSPECTION_DB.INSPECTION_SCHEMA;

-- Instructions for using the application
SELECT 
    'SETUP INSTRUCTIONS' as SECTION,
    'Follow these steps to use the Building Inspection Image Analyzer:' as INSTRUCTIONS
UNION ALL
SELECT 
    'STEP 1',
    'Run this SQL script to create all necessary database objects'
UNION ALL
SELECT 
    'STEP 2', 
    'Deploy the building_inspection_app.py to your Streamlit in Snowflake environment'
UNION ALL
SELECT 
    'STEP 3',
    'Configure the database, schema, and stage names in the Streamlit app sidebar'
UNION ALL
SELECT 
    'STEP 4',
    'Upload building inspection images and analyze them with custom prompts'
UNION ALL
SELECT 
    'STEP 5',
    'View results in the dashboard and export reports as needed'
UNION ALL
SELECT 
    'FEATURES',
    'Image upload to stage, AI analysis, results dashboard, history tracking'
UNION ALL
SELECT 
    'SUPPORTED FORMATS',
    'PNG, JPG, JPEG, TIFF, BMP image files'
UNION ALL
SELECT 
    'AI CAPABILITIES',
    'Powered by Snowflake Cortex for intelligent image analysis'; 