# 🏢 Queensland Building Inspection Image Analyzer

A professional AI-powered building inspection analysis tool built for Queensland Building and Construction Commission (QBCC) standards. This Streamlit application leverages advanced multimodal AI models to analyze building inspection images and provide detailed assessments based on Queensland building codes.

## 🌟 Features

### 📤 Image Processing
- **Multi-format Support**: Upload PNG, JPG, JPEG, TIFF, and BMP images
- **Secure Storage**: Images stored in Snowflake stages with server-side encryption
- **Batch Processing**: Process multiple images simultaneously
- **Database Integration**: Full metadata tracking and storage

### 🤖 AI-Powered Analysis
- **Multiple AI Models**: Support for Claude 3.7 Sonnet, Claude 4 Opus, Claude 4 Sonnet, and OpenAI GPT-4.1
- **Queensland Standards**: Analysis focused on QLD building codes and regulations
- **Confidence Scoring**: AI confidence levels for each analysis
- **Progress Tracking**: Real-time progress bars for multi-image analysis

### 💬 Interactive Chat
- **Image-specific Chat**: Ask questions about specific building inspection images
- **Suggested Questions**: Pre-built questions for common inspection scenarios
- **Chat History**: Persistent conversation history stored in database
- **Real-time Responses**: Immediate AI responses with processing time tracking

### 📊 Professional Dashboard
- **Analysis Results**: Comprehensive view of all inspection analyses
- **Metrics & Statistics**: Database-driven metrics and performance indicators
- **Export Capabilities**: CSV export of analysis results
- **Historical Data**: Complete audit trail of all inspections

### 🔍 Advanced Features
- **Debug Mode**: Comprehensive debugging tools for troubleshooting
- **Stage Management**: Snowflake stage file management and verification
- **Error Handling**: Robust error handling with fallback responses
- **Session Management**: Persistent session state across app usage

## 🛠️ Technology Stack

- **Frontend**: Streamlit with custom CSS styling
- **Backend**: Python with Snowflake integration
- **AI Models**: Anthropic Claude and OpenAI GPT models via Snowflake Cortex
- **Database**: Snowflake with structured schema
- **Storage**: Snowflake stages with encryption
- **Image Processing**: PIL (Python Imaging Library)

## 📋 Prerequisites

- Python 3.8 or higher
- Snowflake account with Cortex AI enabled
- Access to Anthropic Claude and/or OpenAI models
- Required Python packages (see requirements below)

## 🚀 Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd building-inspection-analyzer
   ```

2. **Install dependencies**:
   ```bash
   pip install streamlit pandas numpy pillow snowflake-snowpark-python
   ```

3. **Set up Snowflake connection**:
   - Configure your Snowflake credentials
   - Ensure Cortex AI is enabled in your account
   - Set up appropriate roles and permissions

4. **Initialize the database**:
   ```bash
   # Run the setup script in your Snowflake environment
   # Execute: setup_building_inspection_db.sql
   ```

## 🏗️ Database Setup

The application requires several database objects in Snowflake:

### Required Tables
- `IMAGE_UPLOADS`: Stores image metadata and upload information
- `ANALYSIS_RESULTS`: Stores AI analysis results and confidence scores
- `CHAT_HISTORY`: Stores chat conversations and responses
- `INSPECTION_REPORTS`: Stores comprehensive inspection reports

### Required Stages
- `BUILDING_INSPECTION_STAGE`: Encrypted stage for image storage

### Required Views
- `V_INSPECTION_METRICS`: Aggregated metrics for dashboard

Run the `setup_building_inspection_db.sql` script to create all required objects.

## 🎯 Usage

### Starting the Application
```bash
streamlit run building_inspection_app.py
```

### Configuration
1. **Database Settings**: Select your database, schema, and stage in the sidebar
2. **AI Model**: Choose from available Claude and OpenAI models
3. **Analysis Prompts**: Customize default analysis prompts

### Workflow

#### 1. 📤 Process Images
- Upload building inspection images
- Images are processed and stored securely
- Metadata is recorded in the database

#### 2. 💬 Image Chat
- Select an image from the gallery
- Ask specific questions about the image
- Use suggested questions for common scenarios
- View chat history for each image

#### 3. 🔍 Analyze Images
- Select images for batch analysis
- Customize analysis prompts
- Monitor progress with real-time progress bars
- View detailed analysis results

#### 4. 📊 Results Dashboard
- View comprehensive analysis summaries
- Export results to CSV
- Monitor database metrics
- Access historical data

#### 5. 📋 History
- Filter analyses by date and confidence
- View timeline of all inspections
- Clear history when needed

## 🔧 Configuration Options

### Database Configuration
- **Database Name**: Target Snowflake database
- **Schema Name**: Target schema within database
- **Stage Name**: Snowflake stage for image storage

### AI Model Options
- **claude-3-7-sonnet**: Advanced multimodal reasoning
- **claude-4-opus**: Premium flagship model
- **claude-4-sonnet**: Advanced multimodal reasoning (default)
- **openai-gpt-4.1**: Advanced multimodal AI with vision

### Analysis Configuration
- **Default Prompts**: Customizable analysis prompts
- **Queensland Standards**: Built-in QLD building code references
- **Confidence Thresholds**: Adjustable confidence scoring

## 🎨 User Interface

### Professional Design
- Queensland Government inspired color scheme
- Responsive layout with mobile support
- Professional typography and spacing
- Accessible design principles

### Key UI Components
- **Image Gallery**: 5-column responsive grid
- **Progress Indicators**: Real-time analysis progress
- **Chat Interface**: Professional chat bubbles
- **Metrics Dashboard**: Clean data visualization

## 🔐 Security Features

- **Encrypted Storage**: Server-side encryption for all images
- **Secure Sessions**: Session-based user management
- **Access Control**: Role-based database access
- **Audit Trail**: Complete logging of all operations

## 📊 Monitoring & Analytics

### Built-in Metrics
- Total images processed
- Analysis success rates
- Average confidence scores
- Processing time statistics
- User activity tracking

### Debug Tools
- Image storage verification
- Stage file management
- Database connection testing
- Error logging and reporting

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

### Common Issues

**Images not displaying**: Check stage permissions and image data storage
**AI analysis failing**: Verify Cortex AI is enabled and models are accessible
**Database errors**: Ensure all required tables and objects exist

### Debug Mode
Enable debug mode in the application for detailed troubleshooting information.

### Contact
For support or questions, please create an issue in the repository.

## 🚧 Roadmap

- [ ] Additional AI model support
- [ ] Advanced reporting features
- [ ] Mobile app companion
- [ ] Integration with QBCC systems
- [ ] Automated compliance checking
- [ ] Multi-language support

## 📝 Changelog

### Version 1.0.0
- Initial release with core functionality
- AI-powered image analysis
- Interactive chat interface
- Professional dashboard
- Queensland building standards integration

---

**Built with ❤️ for Queensland Building Professionals**

*This application is designed to assist building inspectors and professionals in Queensland, Australia, with AI-powered analysis tools that complement traditional inspection methods.* 
