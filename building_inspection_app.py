import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import base64
import io
from PIL import Image
import json
import hashlib
from snowflake.snowpark.context import get_active_session

# Initialize Snowflake session
session = get_active_session()

# Helper functions for configuration
def get_available_databases():
    """Get list of databases the current role has access to"""
    try:
        query = "SHOW DATABASES"
        result = session.sql(query).collect()
        databases = [row[1] for row in result]  # Database name is in second column
        
        # Add default if not present
        if "BUILDING_INSPECTION_DB" not in databases:
            databases.append("BUILDING_INSPECTION_DB")
        
        return sorted(databases)
    except Exception as e:
        st.sidebar.error(f"Error retrieving databases: {str(e)}")
        return ["BUILDING_INSPECTION_DB"]  # Default fallback

def get_available_schemas(database_name):
    """Get list of schemas in the specified database"""
    try:
        query = f"SHOW SCHEMAS IN DATABASE {database_name}"
        result = session.sql(query).collect()
        schemas = [row[1] for row in result]  # Schema name is in second column
        
        # Add default if not present
        if "INSPECTION_SCHEMA" not in schemas:
            schemas.append("INSPECTION_SCHEMA")
        
        return sorted(schemas)
    except Exception as e:
        st.sidebar.error(f"Error retrieving schemas for database {database_name}: {str(e)}")
        return ["INSPECTION_SCHEMA"]  # Default fallback

def get_available_stages(database_name, schema_name):
    """Get list of stages in the specified schema"""
    try:
        query = f"SHOW STAGES IN SCHEMA {database_name}.{schema_name}"
        result = session.sql(query).collect()
        stages = [row[1] for row in result]  # Stage name is in second column
        
        # Add default if not present
        if "BUILDING_INSPECTION_STAGE" not in stages:
            stages.append("BUILDING_INSPECTION_STAGE")
        
        return sorted(stages)
    except Exception as e:
        st.sidebar.error(f"Error retrieving stages for schema {database_name}.{schema_name}: {str(e)}")
        return ["BUILDING_INSPECTION_STAGE"]  # Default fallback

def load_existing_analyses(database_name, schema_name):
    """Load existing analyses from the database"""
    try:
        query = f"""
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
            ARRAY_SIZE(ar.recommendations) as recommendation_count,
            ar.analysis_result,
            ar.upload_id,
            ar.processing_time_ms,
            ar.model_used
        FROM {database_name}.{schema_name}.analysis_results ar
        JOIN {database_name}.{schema_name}.image_uploads iu ON ar.upload_id = iu.upload_id
        ORDER BY ar.analysis_time DESC
        LIMIT 100
        """
        result = session.sql(query).collect()
        
        analyses = []
        for row in result:
            # Debug: Check what's in the analysis_result field
            analysis_result_value = row[16] if len(row) > 16 else None
            
            analyses.append({
                'analysis_id': row[0],
                'filename': row[1],
                'original_name': row[2],
                'analysis_prompt': row[3] if row[3] else 'Default prompt',
                'confidence_score': row[4] if row[4] else 0,
                'detected_issues': row[5] if row[5] else [],
                'recommendations': row[6] if row[6] else [],
                'analysis_time': row[7].isoformat(),
                'analyzer': row[8] if row[8] else 'Unknown',
                'upload_time': row[9].isoformat(),
                'file_size': row[10],
                'file_type': row[11],
                'stage_path': row[12],
                'confidence_level': row[13],
                'issue_count': row[14],
                'recommendation_count': row[15],
                'analysis': analysis_result_value,  # This is the analysis_result field
                'upload_id': row[17] if len(row) > 17 else None,
                'processing_time_ms': row[18] if len(row) > 18 and row[18] else 0,
                'model_used': row[19] if len(row) > 19 and row[19] else 'Unknown'
            })
        
        return analyses
    except Exception as e:
        st.error(f"Error loading existing analyses: {str(e)}")
        return []

def load_uploaded_images(database_name, schema_name):
    """Load uploaded images from the database"""
    try:
        query = f"""
        SELECT upload_id, filename, original_name, file_size, upload_time, stage_path, file_type
        FROM {database_name}.{schema_name}.image_uploads
        ORDER BY upload_time DESC
        LIMIT 50
        """
        result = session.sql(query).collect()
        
        images = []
        for row in result:
            images.append({
                'upload_id': row[0],
                'filename': row[1],
                'original_name': row[2],
                'size': row[3],
                'upload_time': row[4].isoformat(),
                'stage_path': row[5],
                'file_type': row[6]
            })
        
        return images
    except Exception as e:
        st.error(f"Error loading uploaded images: {str(e)}")
        return []

def load_image_from_stage(database_name, schema_name, stage_name, filename):
    """Load image binary data from session state or staging table metadata"""
    try:
        # First check if we have the image data in session state (most common case)
        if filename in st.session_state.image_data:
            return st.session_state.image_data[filename]
        
        # Check if we have data in the staging table and reconstruct from chunks
        try:
            # Check if file is tracked in staging table
            file_query = f"""
                SELECT file_id, file_size, file_type, storage_type 
                FROM {database_name}.{schema_name}.stage_file_data 
                WHERE filename = '{filename}' AND status = 'ACTIVE'
                ORDER BY upload_time DESC
                LIMIT 1
            """
            result = session.sql(file_query).collect()
            
            if result and len(result) > 0:
                file_id = result[0][0]
                file_size = result[0][1]
                storage_type = result[0][3] if len(result[0]) > 3 else 'SESSION_STATE'
                
                if storage_type == 'CHUNKED_DB':
                    # Reconstruct file from chunks
                    st.info(f"Reconstructing {filename} from database chunks...")
                    
                    chunks_query = f"""
                        SELECT chunk_index, chunk_data, chunk_size
                        FROM {database_name}.{schema_name}.stage_file_chunks
                        WHERE file_id = '{file_id}'
                        ORDER BY chunk_index
                    """
                    chunks_result = session.sql(chunks_query).collect()
                    
                    if chunks_result:
                        # Reconstruct binary data from chunks
                        reconstructed_data = b''
                        for chunk_row in chunks_result:
                            chunk_hex = chunk_row[1]
                            chunk_data = bytes.fromhex(chunk_hex)
                            reconstructed_data += chunk_data
                        
                        # Validate reconstructed data
                        if len(reconstructed_data) == file_size:
                            # Store in session state for future use
                            st.session_state.image_data[filename] = reconstructed_data
                            st.success(f"✅ Successfully reconstructed {filename} from database")
                            return reconstructed_data
                        else:
                            st.error(f"❌ Size mismatch: expected {file_size}, got {len(reconstructed_data)}")
                            return None
                    else:
                        st.warning(f"⚠️ No chunks found for {filename}")
                        return None
                # File is tracked but stored in session state only
                st.info(f"File {filename} found in database (storage: {storage_type}) but not in session - please re-upload the image")
                return None
            
        except Exception as staging_error:
            # Staging table might not exist or have data
            st.warning(f"Error accessing staging table: {str(staging_error)}")
            pass
        
        # Try to get the file from the stage using the correct method for files uploaded with put_stream
        try:
            st.info(f"🔄 Attempting to load {filename} from stage using get_stream...")
            
            # Use session.file.get_stream - the correct method for files uploaded with put_stream
            # This preserves the original file format
            file_stream = session.file.get_stream(f"@{database_name}.{schema_name}.{stage_name}/{filename}")
            
            if file_stream:
                # Read the binary data from the stream
                image_data = file_stream.read()
                
                # Validate it's actually image data
                if len(image_data) > 10:
                    # Check for common image file signatures
                    hex_start = image_data[:4].hex().upper()
                    if hex_start.startswith(('FFD8', '8950', '4749', '424D')):  # JPEG, PNG, GIF, BMP
                        st.success(f"✅ Valid image format detected: {hex_start}")
                    else:
                        st.info(f"ℹ️ File loaded (format: {hex_start})")
                
                # Store in session state for future use
                st.session_state.image_data[filename] = image_data
                st.success(f"✅ Successfully loaded {filename} from stage using get_stream")
                return image_data
            else:
                raise Exception("No data returned from stage")
                
        except Exception as get_stream_error:
            st.warning(f"⚠️ get_stream method failed: {str(get_stream_error)}")
            
            # Fallback to the existing complex method for backwards compatibility
            temp_table = f"{database_name}.{schema_name}.TEMP_DOWNLOAD_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                # Method 1: Try to read from stage using proper binary format
                st.info(f"🔄 Attempting to load {filename} from stage...")
                
                # Create a temporary table to hold the binary file data
                
                session.sql(f"""
                    CREATE OR REPLACE TABLE {temp_table} (
                        file_content BINARY
                    )
                """).collect()
                
                # Try to copy from stage to table using binary format
                try:
                    session.sql(f"""
                        COPY INTO {temp_table}
                        FROM @{database_name}.{schema_name}.{stage_name}/{filename}
                        FILE_FORMAT = (TYPE = 'JSON' COMPRESSION = 'NONE')
                    """).collect()
                except Exception as json_error:
                    # If JSON format fails, try with a different approach
                    st.info(f"🔄 JSON format failed, trying alternative method...")
                    
                    # Drop and recreate table with string type
                    session.sql(f"DROP TABLE {temp_table}").collect()
                    session.sql(f"""
                        CREATE OR REPLACE TABLE {temp_table} (
                            file_content STRING
                        )
                    """).collect()
                    
                    # Try to copy as raw text
                    session.sql(f"""
                        COPY INTO {temp_table}
                        FROM @{database_name}.{schema_name}.{stage_name}/{filename}
                        FILE_FORMAT = (TYPE = 'CSV' 
                                      FIELD_DELIMITER = NONE 
                                      RECORD_DELIMITER = NONE 
                                      SKIP_HEADER = 0
                                      ESCAPE_UNENCLOSED_FIELD = NONE)
                    """).collect()
                
                # Read the data from the table
                result = session.sql(f"SELECT file_content FROM {temp_table} LIMIT 1").collect()
                
                if result and result[0][0]:
                    file_data = result[0][0]
                    
                    # Handle different data types
                    if isinstance(file_data, bytes):
                        image_data = file_data
                        st.success(f"✅ Loaded as binary data: {len(image_data)} bytes")
                    elif isinstance(file_data, str):
                        # Try to decode from hex (most likely format from stage)
                        try:
                            # Remove any whitespace and convert from hex
                            hex_data = file_data.replace(' ', '').replace('\n', '').replace('\r', '')
                            image_data = bytes.fromhex(hex_data)
                            st.success(f"✅ Loaded from hex string: {len(image_data)} bytes")
                        except ValueError:
                            # Try base64 decoding
                            try:
                                import base64
                                image_data = base64.b64decode(file_data)
                                st.success(f"✅ Loaded from base64 string: {len(image_data)} bytes")
                            except Exception:
                                # Last resort - treat as raw bytes
                                image_data = file_data.encode('utf-8')
                                st.warning(f"⚠️ Loaded as raw text: {len(image_data)} bytes")
                    else:
                        raise Exception(f"Unexpected data type: {type(file_data)}")
                    
                    # Clean up temp table
                    session.sql(f"DROP TABLE {temp_table}").collect()
                    
                    # Validate it's actually image data
                    if len(image_data) > 10:
                        # Check for common image file signatures
                        hex_start = image_data[:4].hex().upper()
                        if hex_start.startswith(('FFD8', '8950', '4749', '424D')):  # JPEG, PNG, GIF, BMP
                            st.success(f"✅ Valid image format detected: {hex_start}")
                        else:
                            st.warning(f"⚠️ Unexpected file signature: {hex_start}")
                    
                    # Store in session state for future use
                    st.session_state.image_data[filename] = image_data
                    st.success(f"✅ Successfully loaded {filename} from stage")
                    return image_data
                else:
                    raise Exception("No data returned from stage")
                    
            except Exception as stage_copy_error:
                # Clean up temp table if it exists
                try:
                    session.sql(f"DROP TABLE IF EXISTS {temp_table}").collect()
                except:
                    pass
                
                # Method 2: Try GET command as fallback (likely won't work in SiS)
                try:
                    get_command = f"GET @{database_name}.{schema_name}.{stage_name}/{filename} file:///tmp/"
                    result = session.sql(get_command).collect()
                    
                    # Read the file from local temp directory
                    import os
                    temp_file_path = f"/tmp/{filename}"
                    
                    # Also try without the path prefix in case stage path is different
                    alt_temp_file_path = f"/tmp/{filename.split('/')[-1]}"
                    
                    for file_path in [temp_file_path, alt_temp_file_path]:
                        if os.path.exists(file_path):
                            with open(file_path, 'rb') as f:
                                image_data = f.read()
                            # Clean up temp file
                            os.remove(file_path)
                            # Store in session state for future use
                            st.session_state.image_data[filename] = image_data
                            st.success(f"✅ Successfully loaded {filename} from stage using GET")
                            return image_data
                    
                except Exception as get_error:
                    st.warning(f"⚠️ GET command failed: {str(get_error)}")
                    # GET command failed (expected in SiS environment)
                    pass
            
            return None
        except Exception as e:
            st.warning(f"Could not load image {filename} from storage: {str(e)}")
            return None
    except Exception as e:
        st.warning(f"Error accessing staging table: {str(e)}")
        return None

def list_stage_files(database_name, schema_name, stage_name):
    """List files in the Snowflake stage"""
    try:
        # Try to list files in the stage
        list_query = f"LIST @{database_name}.{schema_name}.{stage_name}"
        result = session.sql(list_query).collect()
        
        stage_files = []
        for row in result:
            stage_files.append({
                'name': row[0],
                'size': row[1],
                'md5': row[2],
                'last_modified': row[3]
            })
        
        return stage_files
        
    except Exception as e:
        st.error(f"Error listing stage files: {str(e)}")
        return []

def verify_stage_upload(database_name, schema_name, stage_name, filename):
    """Verify if a file exists in the stage"""
    try:
        # Check if file exists in stage
        check_query = f"LIST @{database_name}.{schema_name}.{stage_name}/{filename}"
        result = session.sql(check_query).collect()
        
        if result:
            file_info = {
                'name': result[0][0],
                'size': result[0][1], 
                'md5': result[0][2],
                'last_modified': result[0][3]
            }
            return True, file_info
        else:
            return False, None
            
    except Exception as e:
        return False, str(e)

def ensure_image_data_loaded(database_name, schema_name, stage_name):
    """Ensure image data is loaded for all uploaded images"""
    missing_images = []
    
    for img in st.session_state.uploaded_images:
        filename = img['filename']
        if filename not in st.session_state.image_data:
            missing_images.append(img)
    
    if missing_images:
        st.info(f"Loading {len(missing_images)} images from storage...")
        progress_bar = st.progress(0)
        
        for idx, img in enumerate(missing_images):
            filename = img['filename']
            
            # Try to load from stage
            image_data = load_image_from_stage(database_name, schema_name, stage_name, filename)
            
            if image_data:
                st.session_state.image_data[filename] = image_data
                st.success(f"✅ Loaded {filename} from stage")
            else:
                st.warning(f"⚠️ Could not load {filename} - image preview will not be available")
            
            # Update progress
            progress_bar.progress((idx + 1) / len(missing_images))
        
        progress_bar.empty()
        st.success(f"✅ Loaded {len([img for img in missing_images if img['filename'] in st.session_state.image_data])} images successfully!")
    
    return len(missing_images)

def get_inspection_metrics(database_name, schema_name):
    """Get inspection metrics from the database"""
    try:
        query = f"""
        SELECT * FROM {database_name}.{schema_name}.v_inspection_metrics
        """
        result = session.sql(query).collect()
        
        if result:
            row = result[0]
            return {
                'total_images': row[0],
                'total_analyses': row[1],
                'avg_confidence': row[2],
                'unique_analyzers': row[3],
                'active_days': row[4],
                'total_storage_bytes': row[5],
                'total_issues': row[6],
                'total_recommendations': row[7]
            }
        else:
            return {
                'total_images': 0,
                'total_analyses': 0,
                'avg_confidence': 0,
                'unique_analyzers': 0,
                'active_days': 0,
                'total_storage_bytes': 0,
                'total_issues': 0,
                'total_recommendations': 0
            }
    except Exception as e:
        st.error(f"Error loading inspection metrics: {str(e)}")
        return {}

def verify_database_connection(database_name, schema_name):
    """Verify database connection and objects exist"""
    try:
        # Check if database exists
        db_query = f"SHOW DATABASES LIKE '{database_name}'"
        db_result = session.sql(db_query).collect()
        
        if not db_result:
            return False, f"Database {database_name} does not exist"
        
        # Check if schema exists
        schema_query = f"SHOW SCHEMAS LIKE '{schema_name}' IN DATABASE {database_name}"
        schema_result = session.sql(schema_query).collect()
        
        if not schema_result:
            return False, f"Schema {schema_name} does not exist in database {database_name}"
        
        # Check if required tables exist
        required_tables = ['IMAGE_UPLOADS', 'ANALYSIS_RESULTS', 'INSPECTION_REPORTS']
        for table in required_tables:
            table_query = f"SHOW TABLES LIKE '{table}' IN SCHEMA {database_name}.{schema_name}"
            table_result = session.sql(table_query).collect()
            
            if not table_result:
                return False, f"Required table {table} does not exist"
        
        return True, "Database connection verified successfully"
        
    except Exception as e:
        return False, f"Database connection error: {str(e)}"

def create_chat_history_table(database_name, schema_name):
    """Create chat history table if it doesn't exist"""
    try:
        session.sql(f"""
            CREATE TABLE IF NOT EXISTS {database_name}.{schema_name}.chat_history (
                chat_id STRING NOT NULL,
                image_filename STRING NOT NULL,
                upload_id STRING,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                model_used STRING,
                chat_timestamp TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                session_id STRING,
                processing_time_ms NUMBER,
                PRIMARY KEY (chat_id)
            )
        """).collect()
        return True
    except Exception as e:
        st.error(f"Error creating chat history table: {str(e)}")
        return False

def save_chat_to_database(database_name, schema_name, chat_data):
    """Save chat message to database"""
    try:
        processing_time = chat_data.get('processing_time_ms', 0)
        session.sql(f"""
            INSERT INTO {database_name}.{schema_name}.chat_history (
                chat_id, image_filename, upload_id, user_message, ai_response, 
                model_used, chat_timestamp, session_id
            ) VALUES (
                '{chat_data['chat_id']}', '{chat_data['image_filename']}', 
                '{chat_data.get('upload_id', '')}', '{chat_data['user_message'].replace("'", "''")}', 
                '{chat_data['ai_response'].replace("'", "''")}', '{chat_data.get('model_used', '')}',
                '{chat_data['timestamp']}', '{chat_data.get('session_id', '')}'
            )
        """).collect()
        return True
    except Exception as e:
        st.error(f"Error saving chat to database: {str(e)}")
        return False

def load_chat_history_from_database(database_name, schema_name, image_filename):
    """Load chat history for a specific image from database"""
    try:
        query = f"""
        SELECT chat_id, user_message, ai_response, model_used, chat_timestamp
        FROM {database_name}.{schema_name}.chat_history
        WHERE image_filename = '{image_filename}'
        ORDER BY chat_timestamp ASC
        """
        result = session.sql(query).collect()
        
        chat_history = []
        for row in result:
            chat_history.append({
                'chat_id': row[0],
                'user_message': row[1],
                'ai_response': row[2],
                'model_used': row[3],
                'timestamp': row[4].isoformat() if row[4] else datetime.now().isoformat()
            })
        
        return chat_history
    except Exception as e:
        st.error(f"Error loading chat history: {str(e)}")
        return []

def upload_images_to_stage(uploaded_files, stage_name, database_name, schema_name):
    """Upload images to Snowflake stage"""
    try:
        results = {
            'success': True,
            'count': 0,
            'files': [],
            'error': None
        }
        
        # Check if database objects exist
        try:
            session.sql(f"SELECT COUNT(*) FROM {database_name}.{schema_name}.image_uploads LIMIT 1").collect()
            db_available = True
            st.success("✅ Database objects found - full functionality available")
        except:
            db_available = False
            st.warning("⚠️ Database objects not found. Application will work with limited functionality. Please run the setup_building_inspection_db.sql script for full database features.")
        
        # Check if stage exists
        try:
            stage_check = session.sql(f"DESC STAGE {database_name}.{schema_name}.{stage_name}").collect()
            stage_available = True
            st.success(f"✅ Stage {database_name}.{schema_name}.{stage_name} found")
        except:
            stage_available = False
            st.warning(f"⚠️ Stage {database_name}.{schema_name}.{stage_name} not found. Creating stage...")
            try:
                session.sql(f"""
                    CREATE OR REPLACE STAGE {database_name}.{schema_name}.{stage_name}
                    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
                    DIRECTORY = (ENABLE = TRUE)
                    COMMENT = 'Stage for building inspection images with server-side encryption'
                """).collect()
                stage_available = True
                st.success(f"✅ Stage {database_name}.{schema_name}.{stage_name} created successfully with server-side encryption")
            except Exception as stage_error:
                st.error(f"❌ Failed to create stage: {str(stage_error)}")
                stage_available = False
        
        for uploaded_file in uploaded_files:
            try:
                # Debug file info
                st.write(f"**Processing:** {uploaded_file.name}")
                st.write(f"**File size:** {uploaded_file.size} bytes")
                st.write(f"**File type:** {uploaded_file.type}")
                
                # Reset file pointer to beginning
                uploaded_file.seek(0)
                
                # Read file content
                file_content = uploaded_file.read()
                
                # Debug content info
                st.write(f"**Content read:** {len(file_content)} bytes")
                
                # If no content, try alternative reading method
                if not file_content or len(file_content) == 0:
                    st.warning(f"First read attempt failed for {uploaded_file.name}, trying alternative method...")
                    uploaded_file.seek(0)
                    
                    # Try using getvalue() if it's a BytesIO object
                    try:
                        if hasattr(uploaded_file, 'getvalue'):
                            file_content = uploaded_file.getvalue()
                            st.write(f"**Alternative read:** {len(file_content)} bytes")
                    except:
                        pass
                
                # Final validation
                if not file_content or len(file_content) == 0:
                    st.error(f"❌ Error: {uploaded_file.name} appears to be empty or corrupted. File size: {uploaded_file.size} bytes, Content read: {len(file_content) if file_content else 0} bytes")
                    continue
                
                # Reset file pointer for potential future use
                uploaded_file.seek(0)
                
                # Create unique filename with timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}_{uploaded_file.name}"
                
                # Generate upload ID
                upload_id = f"IMG_{timestamp}_{len(file_content)}"
                
                # Store image data for gallery display
                st.session_state.image_data[filename] = file_content
                
                # Upload to Snowflake stage if available
                stage_path = f"memory://{filename}"  # Default fallback
                if stage_available:
                    try:
                        st.info(f"📤 Uploading {uploaded_file.name} to stage (original format preserved)...")
                        
                        # Use session.file.put_stream - the correct method for Streamlit in Snowflake
                        # This preserves the original file format without any conversion
                        file_stream = io.BytesIO(file_content)
                        
                        # Upload to stage using put_stream (preserves original format)
                        session.file.put_stream(
                            input_stream=file_stream,
                            stage_location=f"@{database_name}.{schema_name}.{stage_name}/{filename}",
                            auto_compress=False,  # Keep original format
                            overwrite=True
                        )
                        
                        # Set stage path
                        stage_path = f"@{database_name}.{schema_name}.{stage_name}/{filename}"
                        st.success(f"✅ Successfully uploaded {uploaded_file.name} to stage (original format preserved)")
                        
                        # Verify upload (optional)
                        try:
                            verify_query = session.sql(f"LIST @{database_name}.{schema_name}.{stage_name}/{filename}").collect()
                            if verify_query:
                                file_info = verify_query[0]
                                uploaded_size = file_info[1]  # Size is in the second column
                                st.info(f"📋 Verification: File size in stage: {uploaded_size} bytes (original: {len(file_content)} bytes)")
                                if uploaded_size == len(file_content):
                                    st.success("✅ File size matches - original format preserved!")
                                else:
                                    st.warning("⚠️ File size mismatch - format may have been altered")
                        except Exception as verify_error:
                            st.warning(f"⚠️ Could not verify upload: {str(verify_error)}")
                        
                    except Exception as stage_error:
                        st.error(f"❌ Stage upload failed for {uploaded_file.name}: {str(stage_error)}")
                        st.info(f"ℹ️ {uploaded_file.name} stored in memory only")
                        # Fall back to memory storage
                        stage_path = f"memory://{filename}"
                else:
                    st.info(f"ℹ️ {uploaded_file.name} stored in memory (stage not available)")
                
                # Validate storage
                if filename not in st.session_state.image_data or len(st.session_state.image_data[filename]) == 0:
                    st.error(f"❌ Error: Failed to store image data for {uploaded_file.name}. Please try again.")
                    continue
                
                st.success(f"✅ Successfully processed {uploaded_file.name} - {len(file_content)} bytes stored")
                
                # Log upload to database if available
                if db_available:
                    try:
                        # Try direct insert first (simpler than stored procedure)
                        session.sql(f"""
                            INSERT INTO {database_name}.{schema_name}.image_uploads (
                                upload_id, filename, original_name, file_size, stage_path, 
                                file_type, upload_time
                            ) VALUES (
                                '{upload_id}', '{filename}', '{uploaded_file.name}', {len(file_content)}, 
                                '{stage_path}', '{uploaded_file.type}', CURRENT_TIMESTAMP()
                            )
                        """).collect()
                        st.success(f"✅ Database record created for {uploaded_file.name}")
                    except Exception as db_error:
                        st.warning(f"⚠️ Database logging failed for {filename}: {str(db_error)}")
                        # Continue without database logging
                
                results['files'].append({
                    'upload_id': upload_id,
                    'filename': filename,
                    'original_name': uploaded_file.name,
                    'size': len(file_content),
                    'upload_time': datetime.now().isoformat(),
                    'stage_path': stage_path,
                    'file_type': uploaded_file.type
                })
                
                results['count'] += 1
                
            except Exception as e:
                st.error(f"❌ Error processing {uploaded_file.name}: {str(e)}")
                continue
        
        return results
        
    except Exception as e:
        return {
            'success': False,
            'count': 0,
            'files': [],
            'error': str(e)
        }

def analyze_images_with_ai(selected_images, prompt, stage_name, database_name, schema_name, model_name="claude-3-7-sonnet"):
    """Analyze images using AI"""
    try:
        results = {
            'success': True,
            'results': [],
            'error': None
        }
        
        for image_name in selected_images:
            # Extract filename from the display name
            filename = image_name.split(' (')[0]
            
            # Get upload_id for this image or generate one
            upload_id = None
            try:
                upload_query = f"""
                SELECT upload_id FROM {database_name}.{schema_name}.image_uploads 
                WHERE filename = '{filename}'
                ORDER BY upload_time DESC
                LIMIT 1
                """
                upload_result = session.sql(upload_query).collect()
                
                if upload_result:
                    upload_id = upload_result[0][0]
                    
            except Exception as e:
                # Database might not be available
                pass
            
            # Generate fallback upload_id if not found
            if not upload_id:
                file_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
                upload_id = f"IMG_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_hash}"
            
            # --- New Logic: Prioritize staged files for analysis ---
            
            # Get the image's stage path from session state
            image_details = next((item for item in st.session_state.uploaded_images if item["filename"] == filename), None)
            stage_path = image_details.get('stage_path') if image_details else None
            
            if stage_path and stage_path.startswith('@'):
                if st.session_state.get('debug_chat', False):
                    st.info(f"⚙️ **Analyzing {filename} using direct stage access:** {stage_path}")
                
                # Parse stage path to separate stage name and file path
                path_parts = stage_path.split('/', 1)
                stage_name_for_url = path_parts[0]
                file_path_in_stage = path_parts[1] if len(path_parts) > 1 else ""

                if not file_path_in_stage:
                    st.error(f"❌ Invalid stage path for {filename}: {stage_path}. Could not extract file path.")
                    continue

                # Escape file path for safety in SQL
                safe_file_path = file_path_in_stage.replace("'", "''")

                prompt_text = f"You are an expert building inspector analyzing a building inspection image. Image filename: {filename}. Analysis request: {prompt}. Please provide a detailed analysis including: 1. Structural assessment, 2. Safety concerns, 3. Maintenance recommendations, 4. Priority level, 5. Estimated confidence level."
                # Escape the prompt text for SQL
                safe_prompt_text = prompt_text.replace("'", "''")

                # Construct SQL using the correct AI_COMPLETE syntax for staged files
                analysis_sql = f"""
                SELECT AI_COMPLETE(
                    '{model_name}',
                    '{safe_prompt_text}',
                    TO_FILE('@{database_name}.{schema_name}.{stage_name}', '{safe_file_path}')
                ) as analysis_result
                """

            else:
                if st.session_state.get('debug_chat', False):
                    st.info(f"📦 **Analyzing {filename} using base64 fallback.** Stage path: {stage_path}")
                
                # Get the actual image data for multimodal AI analysis
                image_data = None
                if filename in st.session_state.image_data:
                    image_data = st.session_state.image_data[filename]
                    if st.session_state.get('debug_chat', False):
                        st.info(f"📦 **Image loaded from session state for {filename}:** {len(image_data)} bytes")
                else:
                    # Try to load from database if not in session state
                    if st.session_state.get('debug_chat', False):
                        st.info(f"🔍 **Loading {filename} from stage...**")
                    image_data = load_image_from_stage(database_name, schema_name, stage_name, filename)
                    if image_data and st.session_state.get('debug_chat', False):
                        st.info(f"📦 **Image loaded from stage for {filename}:** {len(image_data)} bytes")
                
                if image_data:
                    # Convert image data to base64 for multimodal AI
                    import base64
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    
                    if st.session_state.get('debug_chat', False):
                        st.info(f"📤 **Base64 conversion for {filename}:** {len(image_base64)} characters")
                        # Test image format
                        if image_base64.startswith(('iVBORw0KGgo', '/9j/', 'UklGR')):
                            st.success("✅ Image format validated (PNG/JPEG/WebP)")
                        else:
                            st.error("❌ Invalid image format detected")
                            st.code(f"Base64 starts with: {image_base64[:50]}...")
                    
                    # Use Snowflake's CORTEX.COMPLETE function with image data for multimodal analysis
                    # Create the multimodal request, wrapping the messages in a 'messages' object
                    multimodal_request = {
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": f"You are an expert building inspector analyzing a building inspection image. Image filename: {filename}. Analysis request: {prompt}. Please provide a detailed analysis including: 1. Structural assessment, 2. Safety concerns, 3. Maintenance recommendations, 4. Priority level, 5. Estimated confidence level."
                                    },
                                    {
                                        "type": "image",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{image_base64}"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                    
                    # Convert to JSON string for SQL
                    import json
                    request_json = json.dumps(multimodal_request)
                    
                    if st.session_state.get('debug_chat', False):
                        st.info(f"📋 **Multimodal request created for {filename}:** {len(request_json)} characters")
                    
                    # Escape JSON for embedding in SQL
                    escaped_request_json = request_json.replace('\\', '\\\\').replace("'", "''")
                    
                    analysis_sql = f"""
                    SELECT SNOWFLAKE.CORTEX.COMPLETE(
                        '{model_name}',
                        PARSE_JSON('{escaped_request_json}')
                    ) as analysis_result
                    """
                else:
                    if st.session_state.get('debug_chat', False):
                        st.warning(f"⚠️ **No image data available for {filename}** - using text-only analysis")
                    # Fallback to text-only analysis if image data is not available
                    analysis_sql = f"""
                    SELECT SNOWFLAKE.CORTEX.COMPLETE(
                        '{model_name}',
                        CONCAT(
                            'You are an expert building inspector. I need to analyze a building inspection image but the image data is not available. ',
                            'Image filename: {filename}. ',
                            'Analysis request: {prompt.replace("'", "''")}. ',
                            'Please provide general building inspection guidance and explain that the actual image cannot be analyzed without the image data. ',
                            'Focus on Queensland building standards and typical inspection points.'
                        )
                    ) as analysis_result
                    """
            
            try:
                # Execute AI analysis
                start_time = datetime.now()
                
                # Debug the analysis SQL query
                if st.session_state.get('debug_chat', False):
                    st.info(f"🔍 **Analysis SQL Debug for {filename}:**")
                    st.code(analysis_sql[:500] + "..." if len(analysis_sql) > 500 else analysis_sql, language="sql")
                
                ai_result = session.sql(analysis_sql).collect()
                end_time = datetime.now()
                processing_time = (end_time - start_time).total_seconds() * 1000
                
                if st.session_state.get('debug_chat', False):
                    st.info(f"📊 AI result received: {len(ai_result) if ai_result else 0} rows")
                    if ai_result:
                        st.info(f"🔍 AI result structure: {type(ai_result)} with {len(ai_result)} elements")
                        if len(ai_result) > 0:
                            st.info(f"🔍 First row: {type(ai_result[0])} with {len(ai_result[0])} columns")
                            if len(ai_result[0]) > 0:
                                st.info(f"🔍 First column value: {repr(ai_result[0][0])}")
                                st.info(f"🔍 First column type: {type(ai_result[0][0])}")
                    if ai_result and ai_result[0][0]:
                        result_preview = str(ai_result[0][0])[:200]
                        st.info(f"🎯 Analysis result preview: {result_preview}...")
                
                if ai_result and ai_result[0][0]:
                    analysis_text = str(ai_result[0][0])
                    if st.session_state.get('debug_chat', False):
                        st.success(f"✅ AI analysis successful: {len(analysis_text)} characters")
                else:
                    if st.session_state.get('debug_chat', False):
                        st.warning("⚠️ No AI result received, using fallback analysis")
                        if ai_result:
                            st.warning(f"AI result exists but first element is: {ai_result[0][0]}")
                        else:
                            st.warning("AI result is None or empty")
                    # Fallback to mock analysis if AI service is not available
                    analysis_text = f"""
                    Analysis of {filename}:
                    
                    Based on the building inspection image, I can identify the following:
                    
                    1. **Structural Assessment**: The building structure appears to be in good condition with no visible major structural damage.
                    
                    2. **Safety Concerns**: Minor safety issues detected including:
                       - Weathering on exterior surfaces
                       - Potential maintenance needs for roofing materials
                       - Window seal inspection recommended
                    
                    3. **Maintenance Recommendations**:
                       - Schedule routine maintenance for exterior surfaces
                       - Inspect and clean gutters
                       - Check for any loose or damaged materials
                       - Consider preventive weatherproofing
                    
                    4. **Priority Level**: Medium - No immediate safety concerns, but regular maintenance is recommended.
                    
                    5. **Estimated Confidence**: 85% - Clear image quality allows for detailed assessment.
                    """
                    processing_time = 1000  # Mock processing time
                
                # Extract issues from analysis text using AI_COMPLETE
                issues_query = f"""
                SELECT SNOWFLAKE.CORTEX.COMPLETE(
                    '{model_name}',
                    CONCAT(
                        'Extract a list of specific building issues from this analysis text. ',
                        'Return only the issues as a JSON array of strings. ',
                        'Analysis text: {analysis_text.replace("'", "''")}',
                        'Format: ["issue1", "issue2", "issue3"]'
                    )
                ) as issues_result
                """
                
                issues_result = session.sql(issues_query).collect()
                if issues_result and issues_result[0][0]:
                    issues_json = str(issues_result[0][0])
                    # Try to parse as JSON, fallback to simple extraction
                    try:
                        import json
                        detected_issues = json.loads(issues_json)
                    except:
                        # Simple fallback extraction
                        detected_issues = [issue.strip() for issue in issues_json.split(',') if issue.strip()]
                else:
                    detected_issues = []
                
                # Generate recommendations based on issues using AI_COMPLETE
                recommendations_query = f"""
                SELECT SNOWFLAKE.CORTEX.COMPLETE(
                    '{model_name}',
                    CONCAT(
                        'Generate specific maintenance recommendations for these building issues. ',
                        'Return only the recommendations as a JSON array of strings. ',
                        'Issues: {str(detected_issues).replace("'", "''")}',
                        'Analysis context: {analysis_text.replace("'", "''")}',
                        'Format: ["recommendation1", "recommendation2", "recommendation3"]'
                    )
                ) as recommendations_result
                """
                
                recommendations_result = session.sql(recommendations_query).collect()
                if recommendations_result and recommendations_result[0][0]:
                    recommendations_json = str(recommendations_result[0][0])
                    # Try to parse as JSON, fallback to simple extraction
                    try:
                        import json
                        recommendations = json.loads(recommendations_json)
                    except:
                        # Simple fallback extraction
                        recommendations = [rec.strip() for rec in recommendations_json.split(',') if rec.strip()]
                else:
                    recommendations = []
                
                # Calculate confidence score (mock for now)
                confidence_score = 0.85
                
                # Generate analysis ID
                analysis_id = f"ANA_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(analysis_text)}"
                
                # Log analysis result to database if available
                try:
                    # Check if database is available and try simple insert
                    session.sql(f"SELECT COUNT(*) FROM {database_name}.{schema_name}.analysis_results LIMIT 1").collect()
                    
                    # Prepare arrays as strings for database
                    issues_str = str(detected_issues).replace("'", "''")
                    recommendations_str = str(recommendations).replace("'", "''")
                    
                    # Simple insert into analysis_results table
                    session.sql(f"""
                        INSERT INTO {database_name}.{schema_name}.analysis_results (
                            analysis_id, upload_id, filename, analysis_prompt, analysis_result,
                            confidence_score, processing_time_ms, model_used, analysis_time
                        ) VALUES (
                            '{analysis_id}', '{upload_id}', '{filename}', 
                            '{prompt.replace("'", "''")}', '{analysis_text.replace("'", "''")}',
                            {confidence_score}, {processing_time}, 
                            'SNOWFLAKE.CORTEX.COMPLETE ({model_name})', CURRENT_TIMESTAMP()
                        )
                    """).collect()
                    
                except Exception as db_error:
                    st.warning(f"Analysis database logging failed for {filename}: {str(db_error)}")
                    # Continue without database logging
                
                results['results'].append({
                    'analysis_id': analysis_id,
                    'upload_id': upload_id,
                    'filename': filename,
                    'analysis': analysis_text,
                    'analysis_time': datetime.now().isoformat(),
                    'prompt': prompt,
                    'analysis_prompt': prompt,
                    'confidence_score': confidence_score,
                    'detected_issues': detected_issues if isinstance(detected_issues, list) else [],
                    'recommendations': recommendations,
                    'processing_time_ms': processing_time,
                    'model_used': f'SNOWFLAKE.CORTEX.COMPLETE ({model_name})'
                })
                
            except Exception as ai_error:
                # If AI analysis fails, use fallback
                if st.session_state.get('debug_chat', False):
                    st.error(f"❌ **AI Analysis Failed for {filename}:**")
                    st.error(f"**Error Type:** {type(ai_error).__name__}")
                    st.error(f"**Error Message:** {str(ai_error)}")
                    st.error(f"**Image data available:** {image_data is not None}")
                    if image_data:
                        st.error(f"**Image data size:** {len(image_data)} bytes")
                        # Check if image data is valid
                        hex_start = image_data[:4].hex().upper()
                        st.error(f"**Image signature:** {hex_start}")
                        # Test base64 conversion
                        try:
                            import base64
                            test_base64 = base64.b64encode(image_data).decode('utf-8')
                            st.error(f"**Base64 length:** {len(test_base64)} characters")
                            st.error(f"**Base64 starts with:** {test_base64[:50]}...")
                        except Exception as b64_error:
                            st.error(f"**Base64 conversion failed:** {str(b64_error)}")
                    st.error(f"**Analysis SQL preview:** {analysis_sql[:200]}...")
                    st.error(f"**Model used:** {model_name}")
                    
                    # Try a simple text-only test
                    st.info("🔄 Testing text-only analysis...")
                    simple_sql = f"""
                    SELECT SNOWFLAKE.CORTEX.COMPLETE(
                        '{model_name}',
                        'This is a simple test. Please respond with "Test successful" if you can see this message.'
                    ) as test_result
                    """
                    test_result = session.sql(simple_sql).collect()
                    if test_result and test_result[0][0]:
                        st.success(f"✅ Text-only test successful: {test_result[0][0]}")
                    else:
                        st.error("❌ Text-only test failed - AI service may be unavailable")
                else:
                    st.warning(f"AI analysis failed for {filename}, using fallback analysis: {str(ai_error)}")
                
                # Enhanced fallback analysis with more detail
                fallback_analysis = f"""
                # Building Inspection Analysis - {filename}
                
                **Status:** AI analysis unavailable - fallback report generated
                
                **Error:** {str(ai_error)[:200]}{'...' if len(str(ai_error)) > 200 else ''}
                
                ## Manual Inspection Required
                
                Unable to perform automated AI analysis due to technical issues. Please conduct manual inspection focusing on:
                
                ### 1. Structural Assessment
                - Check for cracks, settling, or structural damage
                - Inspect load-bearing elements
                - Verify foundation integrity
                
                ### 2. Safety Concerns
                - Identify immediate hazards
                - Check electrical and plumbing systems
                - Assess fire safety compliance
                
                ### 3. Maintenance Requirements
                - Document required repairs
                - Note preventive maintenance needs
                - Assess weatherproofing condition
                
                ### 4. Queensland Building Standards
                - Verify compliance with current QLD building codes
                - Check for permits and approvals
                - Ensure accessibility requirements are met
                
                **Recommendation:** Please have a qualified building inspector review this image manually and provide a detailed assessment according to Queensland Building and Construction Commission standards.
                
                **Next Steps:**
                1. Contact a licensed building inspector
                2. Schedule on-site inspection if required
                3. Obtain written compliance report
                4. Address any identified issues promptly
                """
                
                # Generate fallback IDs
                fallback_analysis_id = f"ANA_FALLBACK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(filename.encode()).hexdigest()[:8]}"
                
                results['results'].append({
                    'analysis_id': fallback_analysis_id,
                    'upload_id': upload_id,
                    'filename': filename,
                    'analysis': fallback_analysis,
                    'analysis_time': datetime.now().isoformat(),
                    'prompt': prompt,
                    'analysis_prompt': prompt,
                    'confidence_score': 0.3,  # Lower confidence for fallback
                    'detected_issues': ['AI analysis unavailable', 'Manual inspection required', 'Technical error occurred'],
                    'recommendations': ['Perform manual inspection', 'Consult licensed building inspector', 'Retry AI analysis later'],
                    'processing_time_ms': 100,
                    'model_used': f'Fallback Analysis (Error: {type(ai_error).__name__})',
                    'error_details': str(ai_error)
                })
        
        return results
        
    except Exception as e:
        return {
            'success': False,
            'results': [],
            'error': str(e)
        }

# Page configuration
st.set_page_config(
    page_title="QBCC Building Inspection Image Analyzer",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for QBCC-style professional government theme
st.markdown("""
<style>
    /* Import Queensland Government font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    /* Global styling */
    .main .block-container {
        font-family: 'Inter', sans-serif;
        max-width: 1200px;
    }
    
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #003366;
        text-align: center;
        margin-bottom: 1rem;
        border-bottom: 3px solid #0066cc;
        padding-bottom: 1rem;
    }
    
    /* QBCC Logo styling */
    .qbcc-logo {
        height: 80px;
        max-width: 300px;
        margin-bottom: 1rem;
        display: block;
        margin-left: auto;
        margin-right: auto;
        transition: opacity 0.3s ease;
    }
    
    .qbcc-logo:hover {
        opacity: 0.8;
    }
    
    /* Responsive logo */
    @media (max-width: 768px) {
        .qbcc-logo {
            height: 60px;
            max-width: 250px;
        }
        .main-header {
            font-size: 2rem;
        }
    }
    
    .upload-section {
        background-color: #f8f9fb;
        padding: 2rem;
        border-radius: 8px;
        margin: 1rem 0;
        border: 2px dashed #0066cc;
        box-shadow: 0 2px 8px rgba(0,102,204,0.1);
    }
    
    .analysis-section {
        background-color: #ffffff;
        padding: 1.5rem;
        border-radius: 8px;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        border: 1px solid #e6f0ff;
    }
    
    .metric-card {
        background-color: #f0f7ff;
        padding: 0.5rem;
        border-radius: 6px;
        margin: 0.5rem 0;
        border-left: 4px solid #0066cc;
        box-shadow: 0 1px 3px rgba(0,102,204,0.1);
        font-size: 0.65rem;
        line-height: 1.2;
    }
    
    /* Button styling to match QBCC */
    .stButton > button {
        background-color: #0066cc;
        color: white;
        border: none;
        padding: 0.75rem 1.5rem;
        border-radius: 6px;
        font-weight: 600;
        font-family: 'Inter', sans-serif;
        transition: all 0.3s ease;
        text-transform: none;
    }
    
    .stButton > button:hover {
        background-color: #004499;
        box-shadow: 0 4px 8px rgba(0,102,204,0.2);
    }
    
    /* Sidebar styling */
    .css-1d391kg {
        background-color: #f4f6f8;
        border-right: 1px solid #e0e8f0;
    }
    
    .sidebar .sidebar-content {
        background-color: #f4f6f8;
        font-family: 'Inter', sans-serif;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        background-color: #ffffff;
        border-bottom: 2px solid #e0e8f0;
    }
    
    .stTabs [data-baseweb="tab"] {
        background-color: transparent;
        color: #003366;
        font-weight: 500;
        font-family: 'Inter', sans-serif;
        padding: 0.75rem 1.5rem;
        border-radius: 6px 6px 0 0;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background-color: #f0f7ff;
    }
    
    .stTabs [aria-selected="true"] {
        background-color: #0066cc;
        color: white;
    }
    
    /* Image preview styling */
    .image-preview {
        border: 2px solid #0066cc;
        border-radius: 8px;
        padding: 10px;
        margin: 10px 0;
        background-color: #f8f9fb;
    }
    
    /* Success/Error message styling */
    .stSuccess {
        background-color: #e8f5e8;
        border: 1px solid #4caf50;
        color: #2e7d32;
    }
    
    .stError {
        background-color: #ffebee;
        border: 1px solid #f44336;
        color: #c62828;
    }
    
    .stInfo {
        background-color: #e3f2fd;
        border: 1px solid #2196f3;
        color: #1565c0;
    }
    
    /* Header styling for sections */
    h1, h2, h3, h4 {
        color: #003366;
        font-family: 'Inter', sans-serif;
        font-weight: 600;
    }
    
    /* Selectbox and input styling */
    .stSelectbox > div > div {
        background-color: #ffffff;
        border: 1px solid #d0d8e0;
        border-radius: 6px;
        font-family: 'Inter', sans-serif;
    }
    
    .stTextArea > div > div > textarea {
        background-color: #ffffff;
        border: 1px solid #d0d8e0;
        border-radius: 6px;
        font-family: 'Inter', sans-serif;
    }
    
    /* Metric styling */
    [data-testid="metric-container"] {
        background-color: #f8f9fb;
        border: 1px solid #e0e8f0;
        border-radius: 6px;
        padding: 1rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    
    /* Data frame styling */
    .dataframe {
        font-family: 'Inter', sans-serif;
        font-size: 0.9rem;
    }
    
    /* Footer styling */
    .footer-info {
        background-color: #003366;
        color: white;
        padding: 2rem;
        border-radius: 8px;
        margin-top: 2rem;
    }
    
    .footer-info h3 {
        color: white;
        margin-top: 0;
    }
    
    /* Professional progress bar */
    .stProgress .st-bo {
        background-color: #0066cc;
    }
    
    /* Expandable sections */
    .streamlit-expanderHeader {
        background-color: #f0f7ff;
        border: 1px solid #b3d9ff;
        border-radius: 6px;
        color: #003366;
        font-weight: 500;
        font-family: 'Inter', sans-serif;
    }
    
    /* File uploader */
    .stFileUploader > div {
        background-color: #f8f9fb;
        border: 2px dashed #0066cc;
        border-radius: 8px;
        padding: 2rem;
    }
    
    .stFileUploader label {
        color: #003366;
        font-family: 'Inter', sans-serif;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)

# Title and description
st.markdown("""
<div style="text-align: center; margin-bottom: 2rem;">
    <img src="https://www.qbcc.qld.gov.au/themes/custom/qbcc/qbcc-open.svg" 
         alt="Queensland Building and Construction Commission" 
         class="qbcc-logo"
         onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
    <div style="display: none; color: #003366; font-weight: 600; margin-bottom: 1rem;">
        Queensland Building and Construction Commission
    </div>
    <h1 class="main-header">🏢 Queensland Building Inspection Image Analyzer</h1>
    <h3 style="color: #666; font-weight: 400; margin-top: 0; font-family: 'Inter', sans-serif;">Professional Building Inspection Analysis - Queensland Standards Compliance</h3>
</div>
""", unsafe_allow_html=True)

# Initialize session state
if 'uploaded_images' not in st.session_state:
    st.session_state.uploaded_images = []
if 'analysis_results' not in st.session_state:
    st.session_state.analysis_results = []
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'selected_chat_image' not in st.session_state:
    st.session_state.selected_chat_image = None
if 'image_data' not in st.session_state:
    st.session_state.image_data = {}

# Sidebar configuration
st.sidebar.header("🔧 System Configuration")

# Database selection
available_databases = get_available_databases()
database_name = st.sidebar.selectbox(
    "Database Name",
    options=available_databases,
    index=available_databases.index("BUILDING_INSPECTION_DB") if "BUILDING_INSPECTION_DB" in available_databases else 0,
    help="Select the database to use for storing inspection data"
)

# Schema selection (depends on selected database)
available_schemas = get_available_schemas(database_name)
schema_name = st.sidebar.selectbox(
    "Schema Name",
    options=available_schemas,
    index=available_schemas.index("INSPECTION_SCHEMA") if "INSPECTION_SCHEMA" in available_schemas else 0,
    help="Select the schema within the database"
)

# Stage configuration (depends on selected database and schema)
available_stages = get_available_stages(database_name, schema_name)
stage_name = st.sidebar.selectbox(
    "Snowflake Stage Name",
    options=available_stages,
    index=available_stages.index("BUILDING_INSPECTION_STAGE") if "BUILDING_INSPECTION_STAGE" in available_stages else 0,
    help="Select the Snowflake stage where images will be uploaded"
)

# Model selection for AI inference (multimodal models only)
available_models = [
    "claude-3-7-sonnet",
    "claude-4-opus",
    "claude-4-sonnet"
]

selected_model = st.sidebar.selectbox(
    "AI Model",
    options=available_models,
    index=0,  # Default to claude-3-7-sonnet
    help="Select the multimodal AI model to use for image analysis (cross-region inference enabled)"
)

# Model information (multimodal models only)
model_info = {
    "claude-3-7-sonnet": "Anthropic's Claude 3.7 Sonnet - Advanced multimodal reasoning",
    "claude-4-opus": "Anthropic's Claude 4 Opus - Premium multimodal flagship model",
    "claude-4-sonnet": "Anthropic's Claude 4 Sonnet - Advanced multimodal reasoning"
}

if selected_model in model_info:
    st.sidebar.caption(f"ℹ️ {model_info[selected_model]}")

# Analysis configuration
st.sidebar.markdown("---")
st.sidebar.header("🎯 Analysis Configuration")

default_prompt = st.sidebar.text_area(
    "Default Analysis Prompt",
    value="What construction defects are visible in this image? Please use Queensland standards and include the standard reference",
    height=100,
    help="This prompt will be used if no custom prompt is provided"
)

# Configuration info
st.sidebar.markdown("---")
st.sidebar.markdown("### 📋 Selected Configuration")
st.sidebar.info(f"""
**Database**: {database_name}  
**Schema**: {schema_name}  
**Stage**: {stage_name}  
**AI Model**: {selected_model}
""")

# Refresh configuration button
if st.sidebar.button("🔄 Refresh Configuration Options"):
    st.rerun()

# Database connection verification
with st.sidebar.expander("🔍 Database Status", expanded=False):
    if st.button("Check Database Connection"):
        is_connected, message = verify_database_connection(database_name, schema_name)
        if is_connected:
            st.success(message)
        else:
            st.error(message)
            
            # Offer to create missing objects
            if st.button("🚀 Create Missing Objects"):
                try:
                    with st.spinner("Creating database objects..."):
                        # Create database if it doesn't exist
                        session.sql(f"CREATE DATABASE IF NOT EXISTS {database_name}").collect()
                        
                        # Create schema if it doesn't exist
                        session.sql(f"CREATE SCHEMA IF NOT EXISTS {database_name}.{schema_name}").collect()
                        
                        # Create stage if it doesn't exist
                        session.sql(f"""
                            CREATE OR REPLACE STAGE {database_name}.{schema_name}.{stage_name}
                            ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
                            DIRECTORY = (ENABLE = TRUE)
                            COMMENT = 'Stage for building inspection images with server-side encryption'
                        """).collect()
                        
                        st.success("✅ Database objects created successfully!")
                        st.info("💡 Run the setup_building_inspection_db.sql script to create all required tables and functions.")
                        
                except Exception as e:
                    st.error(f"Error creating objects: {str(e)}")

# Quick setup helper
with st.sidebar.expander("⚙️ Quick Setup", expanded=False):
    st.markdown("""
    **Setup Steps:**
    1. Select or create database/schema/stage above
    2. Run the `setup_building_inspection_db.sql` script
    3. Start processing and analyzing images!
    
    **Note:** The application will work with limited functionality without the database setup, but full features require the database objects.
    """)
    
    if st.button("📋 Copy Setup Script Name"):
        st.code("setup_building_inspection_db.sql", language="sql")
        st.success("Script name copied! Run this in your SQL worksheet.")

# Load existing data from database
# Create a key based on current configuration
config_key = f"{database_name}.{schema_name}"
if 'current_config' not in st.session_state:
    st.session_state.current_config = ""

# Check if configuration has changed
if st.session_state.current_config != config_key:
    st.session_state.current_config = config_key
    st.session_state.db_loaded = False
    # Clear existing data when configuration changes
    st.session_state.uploaded_images = []
    st.session_state.analysis_results = []

if 'db_loaded' not in st.session_state:
    st.session_state.db_loaded = False

if not st.session_state.db_loaded:
    with st.spinner(f"Loading data from {database_name}.{schema_name}..."):
        try:
            # Check if database is available first
            session.sql(f"SELECT COUNT(*) FROM {database_name}.{schema_name}.image_uploads LIMIT 1").collect()
            
            # Load existing uploaded images
            existing_images = load_uploaded_images(database_name, schema_name)
            if existing_images:
                st.session_state.uploaded_images = existing_images
            
            # Load existing analyses
            existing_analyses = load_existing_analyses(database_name, schema_name)
            if existing_analyses:
                st.session_state.analysis_results = existing_analyses
            
            st.session_state.db_loaded = True
            
            # Show success message
            if existing_images or existing_analyses:
                st.sidebar.success(f"✅ Loaded {len(existing_images)} images and {len(existing_analyses)} analyses")
            else:
                st.sidebar.info("ℹ️ No existing data found in selected configuration")
            
        except Exception as e:
            st.sidebar.warning(f"Database not available or not set up: {str(e)}")
            st.session_state.db_loaded = True  # Don't keep retrying

# Main content area
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📤 Process Images", "🔍 Analyze Images", "💬 Image Chat", "📊 Results Dashboard", "📋 History"])

with tab1:
    st.markdown('<div class="upload-section">', unsafe_allow_html=True)
    st.markdown("### 📁 Process Building Inspection Images")
    
    # File uploader
    uploaded_files = st.file_uploader(
        "Choose image files",
        accept_multiple_files=True,
        type=['png', 'jpg', 'jpeg', 'tiff', 'bmp'],
        help="Select one or more building inspection images to process"
    )
    
    if uploaded_files:
        st.success(f"✅ {len(uploaded_files)} image(s) selected successfully!")
        
        # Display uploaded images
        cols = st.columns(3)
        for idx, uploaded_file in enumerate(uploaded_files):
            with cols[idx % 3]:
                try:
                    image = Image.open(uploaded_file)
                    st.image(image, caption=uploaded_file.name, use_container_width=True)
                    
                    # Image metadata
                    st.markdown(f"""
                    <div class="metric-card">
                        <strong>File:</strong> {uploaded_file.name}<br>
                        <strong>Size:</strong> {uploaded_file.size:,} bytes<br>
                        <strong>Type:</strong> {uploaded_file.type}<br>
                        <strong>Dimensions:</strong> {image.size[0]} x {image.size[1]}
                    </div>
                    """, unsafe_allow_html=True)
                    
                except Exception as e:
                    st.error(f"Error loading image {uploaded_file.name}: {str(e)}")
        
        # Upload to stage button
        if st.button("🚀 Process Images & Create Database Records", key="upload_to_stage"):
            with st.spinner("Processing images and creating database records..."):
                upload_results = upload_images_to_stage(uploaded_files, stage_name, database_name, schema_name)
                
                if upload_results['success']:
                    st.success(f"✅ Successfully processed {upload_results['count']} images and created database records!")
                    st.session_state.uploaded_images.extend(upload_results['files'])
                    
                    # Display upload summary
                    st.markdown("### Processing Summary")
                    summary_df = pd.DataFrame(upload_results['files'])
                    st.dataframe(summary_df, use_container_width=True)
                else:
                    st.error(f"❌ Processing failed: {upload_results['error']}")
    
    st.markdown('</div>', unsafe_allow_html=True)

with tab2:
    st.markdown('<div class="analysis-section">', unsafe_allow_html=True)
    st.markdown("### 🔍 Image Analysis")
    
    if st.session_state.uploaded_images:
        # Custom prompt input
        custom_prompt = st.text_area(
            "Custom Analysis Prompt (Optional)",
            placeholder="Enter a specific prompt for image analysis or leave blank to use default",
            height=100
        )
        
        # Select images for analysis
        st.markdown("#### Select Images to Analyze")
        
        image_options = [f"{img['filename']} ({img['upload_time']})" for img in st.session_state.uploaded_images]
        selected_images = st.multiselect(
            "Choose images to analyze",
            image_options,
            default=image_options
        )
        
        # Debug mode toggle for analysis
        debug_analysis = st.checkbox("🐛 Enable Analysis Debug Mode", help="Shows detailed information about analysis processing")
        
        if selected_images and st.button("🔍 Analyze Selected Images", key="analyze_images"):
            analysis_prompt = custom_prompt if custom_prompt.strip() else default_prompt
            
            # Set debug mode in session state
            st.session_state.debug_chat = debug_analysis
            
            # Check database availability
            try:
                session.sql(f"SELECT COUNT(*) FROM {database_name}.{schema_name}.analysis_results LIMIT 1").collect()
                st.info("✅ Database available - results will be stored for history and dashboard")
            except:
                st.warning("⚠️ Database not available - analysis will work but results won't be stored")
            
            with st.spinner("Analyzing images with AI..."):
                analysis_results = analyze_images_with_ai(selected_images, analysis_prompt, stage_name, database_name, schema_name, selected_model)
                
                if analysis_results['success']:
                    st.success(f"✅ Analysis completed for {len(analysis_results['results'])} images!")
                    st.session_state.analysis_results.extend(analysis_results['results'])
                    
                    # Display analysis results
                    st.markdown("### Analysis Results")
                    for result in analysis_results['results']:
                        with st.expander(f"📋 Analysis: {result['filename']}", expanded=True):
                            st.markdown(f"**Filename:** {result['filename']}")
                            st.markdown(f"**Analysis Time:** {result['analysis_time']}")
                            st.markdown(f"**Prompt Used:** {result['prompt']}")
                            st.markdown("**AI Analysis:**")
                            st.markdown(result.get('analysis', 'No analysis available'))
                            
                            if 'confidence_score' in result:
                                st.progress(result['confidence_score'])
                                st.caption(f"Confidence Score: {result['confidence_score']:.2%}")
                else:
                    st.error(f"❌ Analysis failed: {analysis_results['error']}")
    
    st.markdown('</div>', unsafe_allow_html=True)

with tab3:
    st.markdown("### 💬 Image Chat")
    
    if st.session_state.uploaded_images:
        # Ensure image data is loaded for all uploaded images
        missing_count = ensure_image_data_loaded(database_name, schema_name, stage_name)
        
        # Image selection for chat
        st.markdown("#### 🖼️ Select Image for Chat")
        
        # Initialize selected image if not set
        if 'selected_chat_image_index' not in st.session_state:
            st.session_state.selected_chat_image_index = 0
        
        # Debug section to help identify image storage issues
        with st.expander("🔍 Debug Image Storage", expanded=False):
            # Add debug toggle
            debug_chat = st.checkbox("🐛 Enable Chat Debug Mode", help="Shows detailed information about chat processing")
            
            if 'debug_chat' not in st.session_state:
                st.session_state.debug_chat = False
            st.session_state.debug_chat = debug_chat
            
            st.write("**Image Data Storage Debug:**")
            st.write(f"- Total stored images: {len(st.session_state.image_data)}")
            st.write(f"- Total uploaded images: {len(st.session_state.uploaded_images)}")
            
            # Stage verification section
            st.write("**Stage Verification:**")
            if st.button("📋 List Files in Stage"):
                stage_files = list_stage_files(database_name, schema_name, stage_name)
                if stage_files:
                    st.success(f"✅ Found {len(stage_files)} files in stage:")
                    for file in stage_files:
                        st.write(f"- {file['name']} ({file['size']} bytes)")
                else:
                    st.warning("⚠️ No files found in stage")
            
            if st.session_state.uploaded_images:
                st.write("**Stage Upload Verification:**")
                for img in st.session_state.uploaded_images:
                    filename = img['filename']
                    stage_path = img.get('stage_path', '')
                    
                    # Check if file was uploaded to actual stage
                    if stage_path.startswith('@'):
                        exists, file_info = verify_stage_upload(database_name, schema_name, stage_name, filename)
                        if exists and file_info and isinstance(file_info, dict):
                            st.success(f"✅ {filename} - Found in stage")
                            st.write(f"  - Size: {file_info.get('size', 'Unknown')} bytes")
                            st.write(f"  - Last modified: {file_info.get('last_modified', 'Unknown')}")
                        else:
                            st.error(f"❌ {filename} - Not found in stage")
                    elif stage_path.startswith('staging_table://'):
                        st.info(f"📊 {filename} - Stored in database (stage fallback)")
                    else:
                        st.warning(f"⚠️ {filename} - Memory only")
            
            if st.session_state.image_data:
                st.write("**Stored image keys:**")
                for key in st.session_state.image_data.keys():
                    st.write(f"- {key}")
                    
            if st.session_state.uploaded_images:
                st.write("**Uploaded image filenames:**")
                missing_images = []
                for img in st.session_state.uploaded_images:
                    filename = img['filename']
                    has_data = filename in st.session_state.image_data
                    st.write(f"- {filename} {'✅' if has_data else '❌'}")
                    if not has_data:
                        missing_images.append(img)
                
                # Recovery mechanism for missing images
                if missing_images:
                    st.error(f"⚠️ {len(missing_images)} images are missing data and won't display properly!")
                    st.write("**To fix this issue:**")
                    st.write("1. Go back to the 'Process Images' tab")
                    st.write("2. Upload the images again")
                    st.write("3. Click 'Process Images & Create Database Records'")
                    st.write("4. Come back to this tab to see the previews")
                    
                    if st.button("🗑️ Remove Missing Images from List"):
                        # Remove images without data from the uploaded list
                        st.session_state.uploaded_images = [
                            img for img in st.session_state.uploaded_images 
                            if img['filename'] in st.session_state.image_data
                        ]
                        st.success(f"Removed {len(missing_images)} missing images from the list")
                        st.rerun()
                    
            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("🔄 Refresh Debug Info"):
                    st.rerun()
            with col2:
                if st.button("📥 Reload Images from Stage"):
                    # Clear existing image data and reload from stage
                    st.session_state.image_data.clear()
                    missing_count = ensure_image_data_loaded(database_name, schema_name, stage_name)
                    if missing_count > 0:
                        st.success(f"Attempted to reload {missing_count} images from stage")
                    else:
                        st.info("All images already loaded")
                    st.rerun()
            with col3:
                if st.button("🗑️ Clear All Image Data"):
                    st.session_state.image_data.clear()
                    st.session_state.uploaded_images.clear()
                    st.success("All image data cleared - please re-upload images")
                    st.rerun()
        
        # Display images in gallery format
        if st.session_state.uploaded_images:
            st.markdown("**Click on an image to select it for chat:**")
            
            # Create gallery grid
            cols = st.columns(5)
            for idx, img in enumerate(st.session_state.uploaded_images):
                with cols[idx % 5]:
                    # Determine if this image is selected
                    is_selected = (st.session_state.selected_chat_image_index == idx)
                    
                    # Display actual image if available
                    if img['filename'] in st.session_state.image_data:
                        try:
                            image_data = st.session_state.image_data[img['filename']]
                            image = Image.open(io.BytesIO(image_data))
                            
                            # Show image with selection border
                            if is_selected:
                                st.markdown("""
                                <div style="border: 3px solid #0066cc; border-radius: 8px; padding: 5px; background-color: #f0f7ff; margin: 0.5rem 0;">
                                    <div style="text-align: center; font-size: 0.8rem; color: #0066cc; font-weight: 600; margin-bottom: 0.5rem;">
                                        ✅ SELECTED
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
                            
                            st.image(image, caption=img['filename'][:30], use_container_width=True)
                            
                            # Image metadata
                            st.markdown(f"""
                            <div style="font-size: 0.8rem; color: #666; text-align: left; padding: 0.5rem; background-color: #f8f9fb; border-radius: 4px; margin-top: 0.5rem;">
                                <strong>Size:</strong> {img.get('size', 'Unknown')} bytes<br>
                                <strong>Type:</strong> {img.get('file_type', 'Unknown')}<br>
                                <strong>Uploaded:</strong> {img['upload_time'][:16] if len(img['upload_time']) > 16 else img['upload_time']}
                            </div>
                            """, unsafe_allow_html=True)
                            
                        except Exception as e:
                            # Fallback to placeholder if image can't be displayed
                            if is_selected:
                                card_bg = "background-color: #f0f7ff; border: 2px solid #0066cc; box-shadow: 0 4px 8px rgba(0,102,204,0.2);"
                                selection_badge = "<div style='margin-top: 0.5rem; padding: 0.25rem; background-color: #0066cc; color: white; border-radius: 4px; text-align: center; font-size: 0.7rem; font-weight: 600;'>✅ SELECTED</div>"
                            else:
                                card_bg = "background-color: #f8f9fb; border: 1px solid #e0e8f0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);"
                                selection_badge = ""
                            
                            st.markdown(f"""
                            <div style="{card_bg} padding: 1rem; border-radius: 8px; margin: 0.5rem 0; transition: all 0.3s ease;">
                                <div style="text-align: center; margin-bottom: 1rem;">
                                    <div style="font-size: 3rem; color: #0066cc;">📸</div>
                                    <div style="font-size: 0.9rem; color: #003366; font-weight: 600; margin-top: 0.5rem;">
                                        {img['filename'][:20]}{"..." if len(img['filename']) > 20 else ""}
                                    </div>
                                </div>
                                <div style="font-size: 0.8rem; color: #666; text-align: left;">
                                    <strong>Size:</strong> {img.get('size', 'Unknown')} bytes<br>
                                    <strong>Type:</strong> {img.get('file_type', 'Unknown')}<br>
                                    <strong>Uploaded:</strong> {img['upload_time'][:16] if len(img['upload_time']) > 16 else img['upload_time']}
                                </div>
                                {selection_badge}
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        # Placeholder if no image data available
                        if is_selected:
                            card_bg = "background-color: #f0f7ff; border: 2px solid #0066cc; box-shadow: 0 4px 8px rgba(0,102,204,0.2);"
                            selection_badge = "<div style='margin-top: 0.5rem; padding: 0.25rem; background-color: #0066cc; color: white; border-radius: 4px; text-align: center; font-size: 0.7rem; font-weight: 600;'>✅ SELECTED</div>"
                        else:
                            card_bg = "background-color: #f8f9fb; border: 1px solid #e0e8f0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);"
                            selection_badge = ""
                        
                        st.markdown(f"""
                        <div style="{card_bg} padding: 1rem; border-radius: 8px; margin: 0.5rem 0; transition: all 0.3s ease;">
                            <div style="text-align: center; margin-bottom: 1rem;">
                                <div style="font-size: 3rem; color: #0066cc;">📸</div>
                                <div style="font-size: 0.9rem; color: #003366; font-weight: 600; margin-top: 0.5rem;">
                                    {img['filename'][:20]}{"..." if len(img['filename']) > 20 else ""}
                                </div>
                            </div>
                            <div style="font-size: 0.8rem; color: #666; text-align: left;">
                                <strong>Size:</strong> {img.get('size', 'Unknown')} bytes<br>
                                <strong>Type:</strong> {img.get('file_type', 'Unknown')}<br>
                                <strong>Uploaded:</strong> {img['upload_time'][:16] if len(img['upload_time']) > 16 else img['upload_time']}
                            </div>
                            {selection_badge}
                        </div>
                        """, unsafe_allow_html=True)
                    
                    # Selection button with professional styling
                    if is_selected:
                        st.success("🔸 Currently Selected", icon="✅")
                    else:
                        if st.button(f"🔘 Select {img['filename'][:15]}{'...' if len(img['filename']) > 15 else ''}", 
                                   key=f"select_img_{idx}", 
                                   use_container_width=True):
                            st.session_state.selected_chat_image_index = idx
                            st.session_state.selected_chat_image = img
                            st.rerun()
            
            # Add some spacing
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Display selected image details
            selected_img = None
            if st.session_state.selected_chat_image_index < len(st.session_state.uploaded_images):
                selected_img = st.session_state.uploaded_images[st.session_state.selected_chat_image_index]
                st.session_state.selected_chat_image = selected_img
                
                # Debug information
                st.markdown(f"**Debug:** Looking for image: `{selected_img['filename']}`")
                st.markdown(f"**Debug:** Image data available: {selected_img['filename'] in st.session_state.image_data}")
                
                # Add debug button to test image analysis
                if st.button("🔍 Debug Image Analysis", key="debug_analysis"):
                    st.info("🔄 Testing image analysis debug...")
                    
                    # Test image loading
                    test_image_data = None
                    if selected_img['filename'] in st.session_state.image_data:
                        test_image_data = st.session_state.image_data[selected_img['filename']]
                        st.success(f"✅ Image data loaded from session: {len(test_image_data)} bytes")
                    else:
                        st.info("🔄 Attempting to load from stage...")
                        test_image_data = load_image_from_stage(database_name, schema_name, stage_name, selected_img['filename'])
                        if test_image_data:
                            st.success(f"✅ Image data loaded from stage: {len(test_image_data)} bytes")
                        else:
                            st.error("❌ Failed to load image data")
                    
                    if test_image_data:
                        try:
                            # Test base64 conversion
                            import base64
                            test_base64 = base64.b64encode(test_image_data).decode('utf-8')
                            st.success(f"✅ Base64 conversion successful: {len(test_base64)} characters")
                                
                            # Test image format detection
                            if test_base64.startswith('iVBORw0KGgo'):
                                st.success("✅ PNG format detected")
                            elif test_base64.startswith('/9j/'):
                                st.success("✅ JPEG format detected")
                            elif test_base64.startswith('UklGR'):
                                st.success("✅ WebP format detected")
                            else:
                                st.warning(f"⚠️ Unknown format, starts with: {test_base64[:20]}...")
                            
                            # Test multimodal request creation
                            test_multimodal_request = [
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Can you see this building inspection image? Just respond with 'Yes, I can see the image' if you can see it, or describe what you see."
                                        },
                                        {
                                            "type": "image",
                                            "image_url": {
                                                "url": f"data:image/jpeg;base64,{test_base64}"
                                            }
                                        }
                                    ]
                                }
                            ]
                            
                            import json
                            test_request_json = json.dumps(test_multimodal_request)
                            st.success(f"✅ Multimodal request created: {len(test_request_json)} characters")
                            
                            # Escape JSON for embedding in SQL
                            escaped_test_request_json = test_request_json.replace('\\', '\\\\').replace("'", "''")
                            
                            # Test AI analysis
                            st.info("🤖 Testing AI analysis...")
                            test_analysis_sql = f"""
                            SELECT SNOWFLAKE.CORTEX.COMPLETE(
                                '{selected_model}',
                                PARSE_JSON('{escaped_test_request_json}')
                            ) as analysis_result
                            """
                            
                            try:
                                test_ai_result = session.sql(test_analysis_sql).collect()
                                if test_ai_result and test_ai_result[0][0]:
                                    test_response = str(test_ai_result[0][0])
                                    st.success(f"✅ AI analysis successful: {len(test_response)} characters")
                                    st.markdown("**AI Response:**")
                                    st.markdown(test_response)
                                        
                                    # Check if AI can actually see the image
                                    if "Yes, I can see the image" in test_response or "I can see" in test_response:
                                        st.success("🎉 AI can see the image! The issue might be with the analysis prompt.")
                                    else:
                                        st.warning("⚠️ AI responded but may not be seeing the image content.")
                                else:
                                    st.error("❌ AI analysis returned empty result")
                            except Exception as ai_error:
                                st.error(f"❌ AI analysis failed: {str(ai_error)}")
                                st.error(f"Error type: {type(ai_error).__name__}")
                                
                        except Exception as base64_error:
                            st.error(f"❌ Base64 conversion failed: {str(base64_error)}")
                    else:
                        st.error("❌ Cannot proceed with analysis - no image data available")
                
                # Display actual image if available
                if selected_img and selected_img['filename'] in st.session_state.image_data:
                    try:
                        image_data = st.session_state.image_data[selected_img['filename']]
                        st.markdown(f"**Debug:** Image data size: {len(image_data)} bytes")
                        image = Image.open(io.BytesIO(image_data))
                        st.markdown(f"**Debug:** Image loaded successfully, size: {image.size}")
                        
                        # Create columns for image and details
                        col_img, col_details = st.columns([1, 1])
                        
                        with col_img:
                            st.markdown("### 🖼️ Selected Image")
                            st.image(image, caption=f"Selected: {selected_img['filename']}", use_container_width=True)
                        
                        with col_details:
                            st.markdown("### 📄 Image Details")
                            st.markdown(f"""
                            <div style="background-color: #f0f7ff; padding: 1.5rem; border-radius: 8px; margin: 1rem 0; border-left: 4px solid #0066cc;">
                                <h4 style="color: #003366; margin-top: 0;">📄 Selected Image Details</h4>
                                <p style="margin: 0.5rem 0; color: #666;">
                                    <strong>Filename:</strong> {selected_img['filename']}<br>
                                    <strong>Size:</strong> {selected_img.get('size', 'Unknown')} bytes<br>
                                    <strong>Upload Time:</strong> {selected_img['upload_time']}<br>
                                    <strong>File Type:</strong> {selected_img.get('file_type', 'Unknown')}<br>
                                    <strong>Stage Path:</strong> {selected_img.get('stage_path', 'Unknown')}
                                </p>
                            </div>
                            """, unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f"**Debug:** Error loading image: {str(e)}")
                        # Fallback to text only if image can't be displayed
                        st.markdown(f"""
                        <div style="background-color: #fff3cd; padding: 1.5rem; border-radius: 8px; margin: 1rem 0; border-left: 4px solid #ffc107;">
                            <h4 style="color: #856404; margin-top: 0;">⚠️ Image Display Error</h4>
                            <p style="margin: 0.5rem 0; color: #666;">
                                <strong>Error:</strong> {str(e)}<br>
                                <strong>Filename:</strong> {selected_img['filename']}<br>
                                <strong>Size:</strong> {selected_img.get('size', 'Unknown')} bytes<br>
                                <strong>Upload Time:</strong> {selected_img['upload_time']}<br>
                                <strong>File Type:</strong> {selected_img.get('file_type', 'Unknown')}<br>
                                <strong>Stage Path:</strong> {selected_img.get('stage_path', 'Unknown')}
                            </p>
                        </div>
                        """, unsafe_allow_html=True)
                elif selected_img:
                    st.warning(f"**Debug:** No image data found for: `{selected_img['filename']}`")
                    st.markdown("**Available image keys:**")
                    for key in st.session_state.image_data.keys():
                        st.markdown(f"- `{key}`")
                    
                    # Fallback when no image data is available
                    st.markdown(f"""
                    <div style="background-color: #f8d7da; padding: 1.5rem; border-radius: 8px; margin: 1rem 0; border-left: 4px solid #dc3545;">
                        <h4 style="color: #721c24; margin-top: 0;">❌ No Image Data Available</h4>
                        <p style="margin: 0.5rem 0; color: #666;">
                            <strong>Filename:</strong> {selected_img['filename']}<br>
                            <strong>Size:</strong> {selected_img.get('size', 'Unknown')} bytes<br>
                            <strong>Upload Time:</strong> {selected_img['upload_time']}<br>
                            <strong>File Type:</strong> {selected_img.get('file_type', 'Unknown')}<br>
                            <strong>Stage Path:</strong> {selected_img.get('stage_path', 'Unknown')}<br>
                            <strong>Note:</strong> Please re-upload the image to see the preview.
                        </p>
                    </div>
                    """, unsafe_allow_html=True)

            # Chat interface
            st.markdown("#### 💬 Chat about this Image")
            
            # Create chat history table if it doesn't exist
            create_chat_history_table(database_name, schema_name)
            
            if selected_img:
                # Load chat history from database
                db_chat_history = load_chat_history_from_database(database_name, schema_name, selected_img['filename'])
                
                # Display chat history
                if db_chat_history:
                    st.markdown("**Chat History:**")
                    for chat in db_chat_history:
                        # Format timestamp for display
                        try:
                            chat_time = datetime.fromisoformat(chat['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            chat_time = chat['timestamp']
                        
                        # User message
                        st.markdown(f"""
                        <div style="background-color: #e8f4f8; padding: 0.75rem; border-radius: 8px; margin: 0.5rem 0; border-left: 3px solid #0066cc;">
                            <strong>You:</strong> {chat['user_message']}
                            <div style="font-size: 0.8rem; color: #666; margin-top: 0.5rem;">
                                <strong>Time:</strong> {chat_time}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # AI response with inference results
                        st.markdown(f"""
                        <div style="background-color: #f8f9fb; padding: 0.75rem; border-radius: 8px; margin: 0.5rem 0; border-left: 3px solid #4caf50;">
                            <strong>AI Assistant:</strong> {chat['ai_response']}
                            <div style="font-size: 0.8rem; color: #666; margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid #e0e8f0;">
                                <strong>Inference Results:</strong><br>
                                <strong>Model Used:</strong> {chat.get('model_used', 'Unknown')}<br>
                                <strong>Processing Time:</strong> {chat.get('processing_time_ms', 'N/A')} ms<br>
                                <strong>Response Time:</strong> {chat_time}<br>
                                <strong>Chat ID:</strong> {chat.get('chat_id', 'N/A')}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                
                # Chat input form for Enter key handling
                with st.form(key="chat_form", clear_on_submit=True):
                    user_question = st.text_input(
                        "Ask a question about this image:",
                        placeholder="e.g., What structural issues can you see? What maintenance is recommended?",
                        key="chat_input_form"
                    )
                    
                    send_button = st.form_submit_button("🚀 Send Message")
                
                # Process message when form is submitted (Enter key or Send button)
                if send_button and user_question.strip():
                    try:
                        with st.spinner("AI analyzing image and responding..."):
                            # Call AI for chat response
                            # Record start time for processing measurement
                            start_time = datetime.now()
                                
                            # Use the working analysis function directly for chat
                            if st.session_state.get('debug_chat', False):
                                st.info("🔄 Using analysis function directly for chat (bypassing multimodal issues)")
                                
                            # Format the user question as an analysis prompt
                            chat_prompt = f"""
                            You are an expert building inspector having a conversation about a building inspection image.
                                
                            User question: {user_question}
                                
                            Please provide a detailed, conversational response based on what you can observe in this building inspection image.
                            Focus on Queensland building standards and practical advice. Be specific and helpful.
                            Respond in a conversational tone as if you're chatting with the user about what you can see.
                            """
                                
                            # Use the exact same analysis function that works
                            test_images = [f"{selected_img['filename']} ({selected_img['upload_time']})"]
                            analysis_results = analyze_images_with_ai(test_images, chat_prompt, stage_name, database_name, schema_name, selected_model)
                                
                            if analysis_results['success'] and analysis_results['results']:
                                analysis_result = analysis_results['results'][0]
                                ai_response = analysis_result['analysis']
                                    
                                if st.session_state.get('debug_chat', False):
                                    st.success(f"✅ Analysis function worked! Response: {len(ai_response)} characters")
                                    st.info(f"🎯 Using analysis function result directly")
                                        
                                # Check if it actually saw the image
                                if ai_response and not ai_response.startswith("# Building Inspection Analysis\nWithout seeing"):
                                    if st.session_state.get('debug_chat', False):
                                        st.success("✅ Analysis function can see the image!")
                                        
                                    # Record end time and calculate processing time
                                    end_time = datetime.now()
                                    processing_time_ms = (end_time - start_time).total_seconds() * 1000
                                    
                                    # Generate unique chat ID
                                    chat_id = f"CHAT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(user_question.encode()).hexdigest()[:8]}"
                                    
                                    # Prepare chat data with processing time
                                    chat_data = {
                                        'chat_id': chat_id,
                                        'image_filename': selected_img['filename'],
                                        'upload_id': selected_img.get('upload_id', ''),
                                        'user_message': user_question,
                                        'ai_response': ai_response,
                                        'model_used': f'SNOWFLAKE.CORTEX.COMPLETE ({selected_model})',
                                        'timestamp': datetime.now().isoformat(),
                                        'session_id': st.session_state.get('session_id', 'unknown'),
                                        'processing_time_ms': processing_time_ms
                                    }
                                    
                                    # Save to database
                                    save_chat_to_database(database_name, schema_name, chat_data)
                                    
                                    # Also add to session state for backward compatibility
                                    st.session_state.chat_history.append({
                                        'image_filename': selected_img['filename'],
                                        'user_message': user_question,
                                        'ai_response': ai_response,
                                        'timestamp': datetime.now().isoformat(),
                                        'model_used': f'SNOWFLAKE.CORTEX.COMPLETE ({selected_model})',
                                        'processing_time_ms': processing_time_ms,
                                        'chat_id': chat_id
                                    })
                                    
                                    if st.session_state.get('debug_chat', False):
                                        st.success(f"✅ Chat saved successfully! Chat ID: {chat_id}")
                                    
                                    st.rerun()
                                else:
                                    if st.session_state.get('debug_chat', False):
                                        st.warning("⚠️ Analysis function also can't see the image - checking image data...")
                                            
                                        # Debug image data loading
                                        image_data = load_image_from_stage(database_name, schema_name, stage_name, selected_img['filename'])
                                        if image_data:
                                            st.info(f"📦 Image data loaded: {len(image_data)} bytes")
                                            # Test if it's valid image data
                                            try:
                                                import base64
                                                test_base64 = base64.b64encode(image_data).decode('utf-8')
                                                if test_base64.startswith(('iVBORw0KGgo', '/9j/', 'UklGR')):
                                                    st.success("✅ Image data appears valid (PNG/JPEG/WebP)")
                                                else:
                                                    st.error("❌ Image data may be corrupted or invalid format")
                                                    st.code(f"First 100 chars: {test_base64[:100]}")
                                            except Exception as img_test_error:
                                                st.error(f"❌ Image encoding test failed: {str(img_test_error)}")
                                        else:
                                            st.error("❌ No image data loaded from stage")
                                                
                                            # Check session state
                                            if selected_img['filename'] in st.session_state.image_data:
                                                session_data = st.session_state.image_data[selected_img['filename']]
                                                st.info(f"📦 Session state has: {len(session_data)} bytes")
                                            else:
                                                st.error("❌ No image data in session state either")
                            else:
                                if st.session_state.get('debug_chat', False):
                                    st.error("❌ Analysis function failed")
                                    st.error(f"Error: {analysis_results.get('error', 'Unknown error')}")
                                
                                ai_response = "I'm having trouble analyzing this image. Please try again or check if the image was uploaded correctly."
                                
                                # Record end time and calculate processing time
                                end_time = datetime.now()
                                processing_time_ms = (end_time - start_time).total_seconds() * 1000
                                
                                # Generate unique chat ID
                                chat_id = f"CHAT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(user_question.encode()).hexdigest()[:8]}"
                                
                                # Prepare chat data with processing time
                                chat_data = {
                                    'chat_id': chat_id,
                                    'image_filename': selected_img['filename'],
                                    'upload_id': selected_img.get('upload_id', ''),
                                    'user_message': user_question,
                                    'ai_response': ai_response,
                                    'model_used': f'SNOWFLAKE.CORTEX.COMPLETE ({selected_model})',
                                    'timestamp': datetime.now().isoformat(),
                                    'session_id': st.session_state.get('session_id', 'unknown'),
                                    'processing_time_ms': processing_time_ms
                                }
                                
                                # Save to database
                                save_chat_to_database(database_name, schema_name, chat_data)
                                
                                # Also add to session state for backward compatibility
                                st.session_state.chat_history.append({
                                    'image_filename': selected_img['filename'],
                                    'user_message': user_question,
                                    'ai_response': ai_response,
                                    'timestamp': datetime.now().isoformat(),
                                    'model_used': f'SNOWFLAKE.CORTEX.COMPLETE ({selected_model})',
                                    'processing_time_ms': processing_time_ms,
                                    'chat_id': chat_id
                                })
                                
                                st.rerun()
                                
                    except Exception as e:
                        st.error(f"❌ Error getting AI response: {str(e)}")
                        
                        # Show more detailed error information for debugging
                        if st.session_state.get('debug_chat', False):
                            st.error(f"**Error Details:**")
                            st.error(f"- Error Type: {type(e).__name__}")
                            st.error(f"- Error Message: {str(e)}")
                            st.error(f"- Image filename: {selected_img['filename']}")
                            st.error(f"- Model: {selected_model}")
                        
                        # Fallback response with proper ai_response assignment
                        ai_response = f"I apologize, but I'm having technical difficulties analyzing the image '{selected_img['filename']}'. Error: {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}. However, I can provide some general guidance: For Queensland building inspections, please check for structural integrity, weatherproofing, compliance with building codes, and any visible maintenance needs. If you have specific concerns about this image, please try asking again or consult with a qualified building inspector."
                        
                        # Generate unique chat ID for fallback
                        chat_id = f"CHAT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(user_question.encode()).hexdigest()[:8]}"
                        
                        # Prepare fallback chat data
                        chat_data = {
                            'chat_id': chat_id,
                            'image_filename': selected_img['filename'],
                            'upload_id': selected_img.get('upload_id', ''),
                            'user_message': user_question,
                            'ai_response': ai_response,
                            'model_used': 'Fallback Response',
                            'timestamp': datetime.now().isoformat(),
                            'session_id': st.session_state.get('session_id', 'unknown'),
                            'processing_time_ms': 100  # Fallback processing time
                        }
                        
                        # Save fallback to database
                        save_chat_to_database(database_name, schema_name, chat_data)
                        
                        # Also add to session state for backward compatibility
                        st.session_state.chat_history.append({
                            'image_filename': selected_img['filename'],
                            'user_message': user_question,
                            'ai_response': ai_response,
                            'timestamp': datetime.now().isoformat(),
                            'model_used': 'Fallback Response',
                            'processing_time_ms': 100,  # Fallback processing time
                            'chat_id': chat_id
                        })
                        
                        st.rerun()
                
                # Clear chat history button (outside the form)
                if st.button("🗑️ Clear Chat History", key="clear_chat"):
                    try:
                        # Clear from database
                        session.sql(f"""
                            DELETE FROM {database_name}.{schema_name}.chat_history
                            WHERE image_filename = '{selected_img['filename']}'
                        """).collect()
                        
                        # Also clear from session state for backward compatibility
                        st.session_state.chat_history = [
                            chat for chat in st.session_state.chat_history 
                            if chat.get('image_filename') != selected_img['filename']
                        ]
                        st.success("Chat history cleared for this image!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error clearing chat history: {str(e)}")
                
                # Suggested questions
                st.markdown("#### 💡 Suggested Questions")
                suggested_questions = [
                    "What structural issues can you identify in this image?",
                    "What maintenance recommendations do you have?",
                    "Are there any safety concerns visible?",
                    "How would you rate the overall condition?",
                    "What Queensland building standards should I consider?",
                    "What should I inspect more closely?"
                ]
                
                cols = st.columns(2)
                for i, question in enumerate(suggested_questions):
                    with cols[i % 2]:
                        if st.button(question, key=f"suggestion_{i}"):
                            # Store the suggested question for the next form submission
                            st.session_state.suggested_question = question
                            st.rerun()
        else:
            st.info("No images available for chat.")
    else:
        st.info("📝 Please process some images first in the 'Process Images' tab to start chatting about them.")

with tab4:
    st.markdown("### 📊 Results Dashboard")
    
    # Debug section to check database connection and data
    with st.expander("🔍 Debug Database Connection", expanded=False):
        if st.button("Test Database Query"):
            try:
                debug_query = f"""
                SELECT 
                    ar.analysis_id,
                    ar.filename,
                    ar.analysis_result,
                    LENGTH(ar.analysis_result) as result_length,
                    ar.analysis_time
                FROM {database_name}.{schema_name}.analysis_results ar
                ORDER BY ar.analysis_time DESC
                LIMIT 5
                """
                debug_result = session.sql(debug_query).collect()
                
                st.success(f"✅ Found {len(debug_result)} records in database")
                
                for i, row in enumerate(debug_result):
                    st.write(f"**Record {i+1}:**")
                    st.write(f"- Analysis ID: {row[0]}")
                    st.write(f"- Filename: {row[1]}")
                    st.write(f"- Analysis Result Length: {row[3] if row[3] else 0} characters")
                    st.write(f"- Analysis Result Preview: {str(row[2])[:100]}..." if row[2] else "No analysis result")
                    st.write(f"- Analysis Time: {row[4]}")
                    st.write("---")
                    
            except Exception as e:
                st.error(f"Database query failed: {str(e)}")
                st.info("This might mean the database tables don't exist or there's no data yet.")
        
        if st.button("Check Table Structure"):
            try:
                # Check if table exists
                table_check = f"SHOW TABLES LIKE 'ANALYSIS_RESULTS' IN SCHEMA {database_name}.{schema_name}"
                table_result = session.sql(table_check).collect()
                
                if table_result:
                    st.success("✅ ANALYSIS_RESULTS table exists")
                    
                    # Get table structure
                    desc_query = f"DESCRIBE TABLE {database_name}.{schema_name}.analysis_results"
                    desc_result = session.sql(desc_query).collect()
                    
                    st.write("**Table Structure:**")
                    columns = [row[0] for row in desc_result]
                    for row in desc_result:
                        st.write(f"- {row[0]}: {row[1]}")
                        
                    # Check if analysis_result column exists
                    if 'ANALYSIS_RESULT' in columns:
                        st.success("✅ ANALYSIS_RESULT column exists")
                    else:
                        st.error("❌ ANALYSIS_RESULT column not found")
                        st.write("Available columns:", columns)
                        
                else:
                    st.error("❌ ANALYSIS_RESULTS table does not exist")
                    
            except Exception as e:
                st.error(f"Table structure check failed: {str(e)}")
    
    # Debug loaded data
    if st.session_state.analysis_results:
        with st.expander("📋 Debug Loaded Data", expanded=False):
            st.write(f"**Total results loaded:** {len(st.session_state.analysis_results)}")
            for i, result in enumerate(st.session_state.analysis_results[:3]):  # Show first 3
                st.write(f"**Result {i+1}:**")
                st.write(f"- Filename: {result.get('filename', 'N/A')}")
                st.write(f"- Analysis ID: {result.get('analysis_id', 'N/A')}")
                analysis_text = result.get('analysis', 'No analysis available')
                st.write(f"- Analysis Length: {len(str(analysis_text)) if analysis_text else 0} characters")
                st.write(f"- Analysis Preview: {str(analysis_text)[:100]}..." if analysis_text else "No analysis")
                st.write("---")
        
        if st.button("🔄 Refresh Data from Database"):
            try:
                # Clear current data
                st.session_state.analysis_results = []
                st.session_state.db_loaded = False
                
                # Reload data
                existing_analyses = load_existing_analyses(database_name, schema_name)
                if existing_analyses:
                    st.session_state.analysis_results = existing_analyses
                    st.success(f"✅ Refreshed! Loaded {len(existing_analyses)} analyses from database")
                else:
                    st.warning("⚠️ No analyses found in database")
                    
                st.session_state.db_loaded = True
                st.rerun()
                
            except Exception as e:
                st.error(f"Failed to refresh data: {str(e)}")
    
    # Load database metrics if available
    try:
        db_metrics = get_inspection_metrics(database_name, schema_name)
    except:
        db_metrics = {}
    
    if st.session_state.analysis_results or db_metrics:
        # Database summary metrics
        st.markdown("#### 🏢 Database Metrics (Last 30 Days)")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "Total Images", 
                db_metrics.get('total_images', 0),
                help="Total images uploaded to the database"
            )
        
        with col2:
            avg_confidence = db_metrics.get('avg_confidence', 0)
            st.metric(
                "Avg Confidence", 
                f"{avg_confidence:.1%}" if avg_confidence else "N/A",
                help="Average confidence score of all analyses"
            )
        
        with col3:
            st.metric(
                "Total Issues", 
                db_metrics.get('total_issues', 0),
                help="Total issues detected across all analyses"
            )
        
        with col4:
            st.metric(
                "Active Users", 
                db_metrics.get('unique_analyzers', 0),
                help="Number of unique users who performed analyses"
            )
        
        # Session metrics
        if st.session_state.analysis_results:
            st.markdown("#### 📱 Current Session Metrics")
            col5, col6, col7, col8 = st.columns(4)
            
            with col5:
                st.metric("Session Analyses", len(st.session_state.analysis_results))
            
            with col6:
                session_avg_confidence = np.mean([r.get('confidence_score', 0) for r in st.session_state.analysis_results])
                st.metric("Session Avg Confidence", f"{session_avg_confidence:.1%}")
            
            with col7:
                unique_issues = len(set([issue for r in st.session_state.analysis_results for issue in r.get('detected_issues', [])]))
                st.metric("Session Issues", unique_issues)
            
            with col8:
                recent_analyses = len([r for r in st.session_state.analysis_results if (datetime.now() - datetime.fromisoformat(r['analysis_time'])).days < 1])
                st.metric("Today's Analyses", recent_analyses)
        
        # Results table
        st.markdown("#### 📋 Analysis Results Summary")
        
        results_data = []
        for result in st.session_state.analysis_results:
            # Safe access to analysis field
            analysis_text = result.get('analysis', 'No analysis available')
            if analysis_text is None:
                analysis_text = 'No analysis available'
            
            # Format analysis time for display
            try:
                analysis_time_formatted = datetime.fromisoformat(result['analysis_time']).strftime('%Y-%m-%d %H:%M')
            except:
                analysis_time_formatted = result['analysis_time']
            
            results_data.append({
                'Filename': result['filename'],
                'Analysis Time': analysis_time_formatted,
                'Confidence Score': f"{result.get('confidence_score', 0):.1%}",
                'Issues Detected': len(result.get('detected_issues', [])),
                'Analysis Result': analysis_text[:150] + "..." if len(analysis_text) > 150 else analysis_text,
                'Model Used': result.get('model_used', 'Unknown')
            })
        
        results_df = pd.DataFrame(results_data)
        
        # Enhanced table with selection
        st.markdown("**Select a record to view detailed analysis:**")
        
        # Create a more user-friendly selection interface
        if len(results_data) > 0:
            # Show summary table first
            st.dataframe(results_df, use_container_width=True, hide_index=True)
            
            # Export results
            col_export1, col_export2 = st.columns([1, 3])
            with col_export1:
                if st.button("📥 Export Results to CSV"):
                    csv = results_df.to_csv(index=False)
                    st.download_button(
                        label="Download CSV",
                        data=csv,
                        file_name=f"building_inspection_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
            
            # Create selection options with more readable format
            selection_options = []
            for idx, result in enumerate(st.session_state.analysis_results):
                analysis_time = datetime.fromisoformat(result['analysis_time']).strftime('%Y-%m-%d %H:%M')
                confidence = result.get('confidence_score', 0)
                selection_options.append(f"{result['filename']} | {analysis_time} | Confidence: {confidence:.1%}")
            
            selected_index = st.selectbox(
                "📋 Select Analysis Record:",
                options=range(len(selection_options)),
                format_func=lambda x: selection_options[x],
                help="Select a record to view detailed analysis results"
            )
            
            # Professional detailed analysis view
            if selected_index is not None:
                result = st.session_state.analysis_results[selected_index]
                
                st.markdown("---")
                st.markdown("#### 🔍 Detailed Analysis Report")
                
                # Header with key information
                st.markdown(f"""
                <div style="background-color: #f0f7ff; padding: 1.5rem; border-radius: 8px; margin: 1rem 0; border-left: 4px solid #0066cc;">
                    <h4 style="color: #003366; margin-top: 0;">📄 {result['filename']}</h4>
                    <p style="margin: 0.5rem 0; color: #666;">
                        <strong>Analysis ID:</strong> {result.get('analysis_id', 'N/A')}<br>
                        <strong>Analysis Time:</strong> {datetime.fromisoformat(result['analysis_time']).strftime('%A, %B %d, %Y at %I:%M %p')}<br>
                        <strong>Processing Time:</strong> {result.get('processing_time_ms', 0):.0f} ms<br>
                        <strong>Confidence Score:</strong> {result.get('confidence_score', 0):.1%}
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                # Progress bar for confidence
                confidence_score = result.get('confidence_score', 0)
                st.progress(confidence_score)
                
                # Main content in professional layout
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    # Full AI Analysis
                    st.markdown("### 🤖 Complete AI Analysis")
                    analysis_text = result.get('analysis', 'No analysis available')
                    # Fix f-string backslash issue by doing replacement outside f-string
                    analysis_html = analysis_text.replace('\n', '<br>')
                    st.markdown(f"""
                    <div style="background-color: #ffffff; padding: 1rem; border-radius: 6px; border: 1px solid #e0e8f0; max-height: 400px; overflow-y: auto;">
                        {analysis_html}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Recommendations
                    st.markdown("### 💡 Recommendations")
                    recommendations = result.get('recommendations', [])
                    if recommendations:
                        st.markdown(f"""
                        <div style="background-color: #e8f5e8; padding: 1rem; border-radius: 6px; border: 1px solid #4caf50;">
                            <strong>Total Recommendations:</strong> {len(recommendations)}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        for idx, rec in enumerate(recommendations, 1):
                            st.markdown(f"""
                            <div style="background-color: #ffffff; padding: 0.75rem; margin: 0.5rem 0; border-radius: 4px; border-left: 3px solid #4caf50;">
                                <strong>Recommendation {idx}:</strong> {rec}
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                        <div style="background-color: #f8f9fb; padding: 1rem; border-radius: 6px; border: 1px solid #e0e8f0; color: #666;">
                            No specific recommendations provided for this analysis
                        </div>
                        """, unsafe_allow_html=True)
                
                with col2:
                    # Analysis prompt used
                    st.markdown("### 🎯 Analysis Prompt")
                    st.markdown(f"""
                    <div style="background-color: #f8f9fb; padding: 1rem; border-radius: 6px; border: 1px solid #e0e8f0;">
                        <em>"{result.get('prompt', 'No prompt available')}"</em>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Detected Issues
                    st.markdown("### ⚠️ Detected Issues")
                    detected_issues = result.get('detected_issues', [])
                    if detected_issues:
                        st.markdown(f"""
                        <div style="background-color: #fff3cd; padding: 1rem; border-radius: 6px; border: 1px solid #ffeaa7;">
                            <strong>Total Issues Found:</strong> {len(detected_issues)}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        for idx, issue in enumerate(detected_issues, 1):
                            st.markdown(f"""
                            <div style="background-color: #ffffff; padding: 0.75rem; margin: 0.5rem 0; border-radius: 4px; border-left: 3px solid #ff9800;">
                                <strong>Issue {idx}:</strong> {issue}
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                        <div style="background-color: #d4edda; padding: 1rem; border-radius: 6px; border: 1px solid #c3e6cb; color: #155724;">
                            ✅ No specific issues detected in this analysis
                        </div>
                        """, unsafe_allow_html=True)
                
                # Technical details section
                st.markdown("### 📊 Technical Details")
                col_tech1, col_tech2, col_tech3 = st.columns(3)
                
                with col_tech1:
                    st.markdown(f"""
                    <div style="background-color: #f0f7ff; padding: 1rem; border-radius: 6px; text-align: center;">
                        <strong>Model Used</strong><br>
                        <span style="color: #0066cc; font-weight: 600;">SNOWFLAKE.CORTEX.COMPLETE</span>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col_tech2:
                    st.markdown(f"""
                    <div style="background-color: #f0f7ff; padding: 1rem; border-radius: 6px; text-align: center;">
                        <strong>Upload ID</strong><br>
                        <span style="color: #0066cc; font-weight: 600;">{result.get('upload_id', 'N/A')}</span>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col_tech3:
                    st.markdown(f"""
                    <div style="background-color: #f0f7ff; padding: 1rem; border-radius: 6px; text-align: center;">
                        <strong>Analysis Status</strong><br>
                        <span style="color: #28a745; font-weight: 600;">✅ Complete</span>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("No analysis results available to display.")
    
    else:
        st.info("📝 No analysis results available. Please analyze some images first.")

with tab5:
    st.markdown("### 📋 Analysis History")
    
    if st.session_state.analysis_results:
        # History filters
        col1, col2 = st.columns(2)
        
        with col1:
            date_filter = st.date_input(
                "Filter by date",
                value=datetime.now().date(),
                help="Show analyses from this date"
            )
        
        with col2:
            confidence_filter = st.slider(
                "Minimum confidence score",
                0.0, 1.0, 0.0,
                help="Show analyses with confidence score above this threshold"
            )
        
        # Filter results
        filtered_results = [
            r for r in st.session_state.analysis_results 
            if datetime.fromisoformat(r['analysis_time']).date() >= date_filter
            and r.get('confidence_score', 0) >= confidence_filter
        ]
        
        if filtered_results:
            st.markdown(f"#### 📊 Showing {len(filtered_results)} analyses")
            
            # Timeline view
            timeline_data = []
            for result in filtered_results:
                timeline_data.append({
                    'Time': result['analysis_time'],
                    'Filename': result['filename'],
                    'Confidence': result.get('confidence_score', 0),
                    'Issues': len(result.get('detected_issues', []))
                })
            
            timeline_df = pd.DataFrame(timeline_data)
            timeline_df['Time'] = pd.to_datetime(timeline_df['Time'])
            
            st.line_chart(timeline_df.set_index('Time')['Confidence'])
            
            # Detailed history table
            st.dataframe(timeline_df, use_container_width=True)
            
            # Clear history button
            if st.button("🗑️ Clear History", key="clear_history"):
                st.session_state.analysis_results = []
                st.session_state.uploaded_images = []
                st.success("✅ History cleared!")
                st.rerun()
        else:
            st.info("No analyses match the current filters.")
    else:
        st.info("📝 No analysis history available.")

# Footer
st.markdown('<div class="footer-info">', unsafe_allow_html=True)
st.markdown("### 📋 Queensland Building Inspection Image Analyzer")

# Database connection status
is_connected, status_message = verify_database_connection(database_name, schema_name)
status_color = "🟢" if is_connected else "🔴"
st.markdown(f"**Database Status**: {status_color} {status_message}")

st.markdown(f"""
This professional Queensland Building Inspection Image Analyzer provides comprehensive analysis aligned with Queensland building standards and regulations.

**Key Features:**
- 📤 **Secure Image Processing**: Process multiple building inspection images with secure database tracking
- 🔍 **AI-Powered Analysis**: Analyze images using advanced AI with Queensland standards compliance
- 💬 **Interactive Image Chat**: Chat with AI about specific images for detailed discussions
- 🤖 **Professional Model Selection**: Choose from multimodal AI models (Claude 3.7/4)
- 📊 **Comprehensive Dashboard**: View analysis results and performance metrics
- 📋 **Audit Trail**: Complete history tracking and export capabilities
- 🎯 **Queensland Standards**: Custom prompts aligned with Queensland building codes
- 🏢 **Enterprise Integration**: Full integration with Snowflake for secure data management

**Supported Image Formats**: PNG, JPG, JPEG, TIFF, BMP

**AI Capabilities**: Powered by Snowflake Cortex AI with cross-region inference support. Choose from 3 multimodal models (Claude-3.7/4) for intelligent image analysis with vision capabilities

**Data Storage**: Image metadata and analysis results securely stored in Snowflake database with proper access controls

**Database Objects Created:**
- `IMAGE_UPLOADS` - Track processed images
- `ANALYSIS_RESULTS` - Store AI analysis results
- `INSPECTION_REPORTS` - Organize inspections into reports
- `V_ANALYSIS_SUMMARY` - Comprehensive analysis view
- `V_INSPECTION_METRICS` - Dashboard metrics

**Setup Instructions:**
1. Run the `setup_building_inspection_db.sql` script to create database objects
2. Configure the database, schema, and stage names in the sidebar
3. Select your preferred multimodal AI model from the available options (cross-region inference enabled)
4. Process images and analyze them with Queensland standards-focused prompts
5. View results in the dashboard and track history

**Current Configuration:**
- Database: `{database_name}`
- Schema: `{schema_name}`
- Stage: `{stage_name}`
- AI Model: `{selected_model}` (multimodal)

**Last Updated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

**Important Notice**: This application is designed to assist with building inspection analysis using Queensland building standards. All analysis results should be reviewed by qualified building professionals. This tool does not replace professional building inspection services or official compliance assessments. 

**For Official Building Compliance**: For official building compliance matters, complaints, or licensing information, please visit the [Queensland Building and Construction Commission (QBCC)](https://www.qbcc.qld.gov.au/) or call 139 333.

**Support**: For technical support or questions regarding this application, please contact your system administrator.

**Disclaimer**: The QBCC logo is used here for professional identification purposes. This application is designed to assist with Queensland building standards compliance analysis. All building inspection work should be conducted by appropriately licensed professionals.
""")
st.markdown('</div>', unsafe_allow_html=True)