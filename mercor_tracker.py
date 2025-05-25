#!/usr/bin/env python3
"""
Mercor Job Tracker - Complete Job Monitoring System
Single backend file with Telegram notifications and Flask web interface
"""

import os
import json
import time
import requests
import logging
import threading
from datetime import datetime
from typing import Dict, List, Set
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request
import subprocess
import sys

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mercor_tracker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class JobMonitor:
    def __init__(self):
        self.api_url = os.getenv('MERCOR_API_URL', 'https://aws.api.mercor.com/work/listings-public?format=json&search=')
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.check_interval = int(os.getenv('CHECK_INTERVAL', '60'))
        self.jobs_file = 'known_jobs.json'
        self.running = False
        
        # Validate required environment variables
        if not self.telegram_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set - Telegram notifications disabled")
        if not self.telegram_chat_id:
            logger.warning("TELEGRAM_CHAT_ID not set - Telegram notifications disabled")
        
        self.known_jobs: Set[str] = self.load_known_jobs()
        logger.info(f"Initialized with {len(self.known_jobs)} known jobs")
        
        # Store current jobs for web interface
        self.current_jobs = []
        self.last_check_time = None
    
    def load_known_jobs(self) -> Set[str]:
        """Load previously seen job IDs from file"""
        try:
            if os.path.exists(self.jobs_file):
                with open(self.jobs_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('job_ids', []))
        except Exception as e:
            logger.error(f"Error loading known jobs: {e}")
        return set()
    
    def save_known_jobs(self):
        """Save current known job IDs to file"""
        try:
            with open(self.jobs_file, 'w') as f:
                json.dump({
                    'job_ids': list(self.known_jobs),
                    'last_updated': datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving known jobs: {e}")
    
    def fetch_jobs(self) -> List[Dict]:
        """Fetch current job listings from Mercor API"""
        try:
            response = requests.get(self.api_url, timeout=30)
            response.raise_for_status()
            jobs = response.json()
            self.current_jobs = jobs
            self.last_check_time = datetime.now()
            return jobs
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching jobs: {e}")
            return []
    
    def send_telegram_message(self, message: str):
        """Send notification via Telegram"""
        if not self.telegram_token or not self.telegram_chat_id:
            logger.info("Telegram not configured - skipping notification")
            return
            
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Telegram notification sent successfully")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Telegram message: {e}")
    
    def format_job_message(self, job: Dict) -> str:
        """Format job posting for Telegram message"""
        title = job.get('title', 'Unknown Title')
        rate_min = job.get('rateMin', 0)
        rate_max = job.get('rateMax', 0)
        location = job.get('location', 'Unknown')
        commitment = job.get('commitment', 'Unknown')
        created_at = job.get('createdAt', '')
        listing_id = job.get('listingId', '')
        
        # Format rate information
        if rate_min and rate_max:
            rate_info = f"${rate_min}-${rate_max}/hr"
        elif rate_min:
            rate_info = f"${rate_min}+/hr"
        else:
            rate_info = "Rate not specified"
        
        # Format creation date
        try:
            if created_at:
                created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                date_str = created_date.strftime('%Y-%m-%d %H:%M UTC')
            else:
                date_str = "Unknown"
        except:
            date_str = "Unknown"
        
        # Truncate description for readability
        description = job.get('description', '')
        if len(description) > 300:
            description = description[:300] + "..."
        
        # Job URL
        job_url = f"https://work.mercor.com/jobs/{listing_id}" if listing_id else ""
        
        message = f"""
🆕 <b>New Job Alert!</b>

<b>{title}</b>
💰 {rate_info}
📍 {location}
⏰ {commitment.title()}
📅 Posted: {date_str}

{description}

{f'🔗 <a href="{job_url}">Apply Here</a>' if job_url else ''}
<i>Job ID: {listing_id}</i>
        """.strip()
        
        return message
    
    def check_for_new_jobs(self):
        """Check for new job postings and send notifications"""
        logger.info("Checking for new jobs...")
        
        current_jobs = self.fetch_jobs()
        if not current_jobs:
            logger.warning("No jobs fetched - API might be down")
            return
        
        current_job_ids = {job.get('listingId') for job in current_jobs if job.get('listingId')}
        new_job_ids = current_job_ids - self.known_jobs
        
        if new_job_ids:
            logger.info(f"Found {len(new_job_ids)} new job(s)")
            
            # Send notification for each new job
            for job in current_jobs:
                job_id = job.get('listingId')
                if job_id in new_job_ids:
                    message = self.format_job_message(job)
                    self.send_telegram_message(message)
                    time.sleep(1)  # Rate limiting
            
            # Update known jobs
            self.known_jobs.update(new_job_ids)
            self.save_known_jobs()
            
            # Send summary if multiple jobs
            if len(new_job_ids) > 1:
                summary = f"📊 Summary: {len(new_job_ids)} new jobs found!"
                self.send_telegram_message(summary)
        else:
            logger.info("No new jobs found")
    
    def start_monitoring(self):
        """Start the monitoring loop in background"""
        self.running = True
        
        # Send startup notification
        if self.telegram_token and self.telegram_chat_id:
            startup_msg = f"🤖 Job Monitor Started!\nMonitoring Mercor API every {self.check_interval} seconds..."
            self.send_telegram_message(startup_msg)
        
        while self.running:
            try:
                self.check_for_new_jobs()
                logger.info(f"Sleeping for {self.check_interval} seconds...")
                time.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(10)  # Wait before retrying
    
    def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.running = False
        if self.telegram_token and self.telegram_chat_id:
            self.send_telegram_message("🛑 Job Monitor Stopped")

# Global monitor instance
monitor = JobMonitor()
monitor_thread = None

# Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-key-change-me')

@app.route('/')
def dashboard():
    """Main dashboard page"""
    stats = get_monitor_stats()
    return render_template('dashboard.html', stats=stats)

@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    return jsonify(get_monitor_stats())

@app.route('/api/jobs')
def api_jobs():
    """API endpoint for current jobs"""
    try:
        jobs = monitor.current_jobs
        return jsonify({
            'success': True,
            'count': len(jobs),
            'jobs': jobs[:20]  # Return first 20 jobs
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/logs')
def api_logs():
    """API endpoint for recent logs"""
    try:
        if os.path.exists('mercor_tracker.log'):
            with open('mercor_tracker.log', 'r') as f:
                lines = f.readlines()
                recent_logs = lines[-50:]  # Last 50 lines
        else:
            recent_logs = ["No logs available yet"]
        
        return jsonify({
            'success': True,
            'logs': [line.strip() for line in recent_logs]
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/test-telegram', methods=['POST'])
def test_telegram():
    """Test Telegram connection"""
    try:
        test_message = "🧪 Test message from Mercor Job Tracker web interface"
        monitor.send_telegram_message(test_message)
        return jsonify({'success': True, 'message': 'Test message sent!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/force-check', methods=['POST'])
def force_check():
    """Force a job check"""
    try:
        monitor.check_for_new_jobs()
        return jsonify({'success': True, 'message': 'Job check completed!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/start-monitor', methods=['POST'])
def start_monitor():
    """Start monitoring"""
    global monitor_thread
    try:
        if monitor_thread and monitor_thread.is_alive():
            return jsonify({'success': False, 'error': 'Monitor already running'})
        
        monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
        monitor_thread.start()
        return jsonify({'success': True, 'message': 'Monitor started!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stop-monitor', methods=['POST'])
def stop_monitor():
    """Stop monitoring"""
    try:
        monitor.stop_monitoring()
        return jsonify({'success': True, 'message': 'Monitor stopped!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def get_monitor_stats():
    """Get monitoring statistics"""
    global monitor_thread
    
    stats = {
        'monitor_running': monitor_thread and monitor_thread.is_alive() if monitor_thread else False,
        'known_jobs_count': len(monitor.known_jobs),
        'check_interval': monitor.check_interval,
        'last_check': 'Never',
        'current_jobs_count': len(monitor.current_jobs),
        'telegram_configured': bool(monitor.telegram_token and monitor.telegram_chat_id)
    }
    
    # Get last check time
    if monitor.last_check_time:
        stats['last_check'] = monitor.last_check_time.strftime('%Y-%m-%d %H:%M:%S')
    
    # Try to get last update time from known_jobs.json
    try:
        if os.path.exists('known_jobs.json'):
            with open('known_jobs.json', 'r') as f:
                data = json.load(f)
                if 'last_updated' in data:
                    last_updated = datetime.fromisoformat(data['last_updated'])
                    stats['last_update'] = last_updated.strftime('%Y-%m-%d %H:%M:%S')
    except:
        pass
    
    return stats

def setup_environment():
    """Setup environment and check requirements"""
    # Check if .env exists
    if not os.path.exists('.env'):
        print("Creating .env file...")
        with open('.env', 'w') as f:
            f.write("""# Mercor Job Tracker Configuration

# Telegram Bot Configuration (Optional - for notifications)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Monitor Settings
CHECK_INTERVAL=60

# Flask Settings
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=False

# Mercor API
MERCOR_API_URL=https://aws.api.mercor.com/work/listings-public?format=json&search=
""")
        print("✅ .env file created")
    
    # Check and install requirements
    try:
        import requests
        import flask
        import dotenv
        print("✅ All requirements satisfied")
    except ImportError as e:
        print(f"❌ Missing requirement: {e}")
        print("📦 Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "flask", "python-dotenv"])
        print("✅ Requirements installed")

def main():
    """Main function to run the application"""
    global monitor_thread
    
    print("🤖 Mercor Job Tracker Starting...")
    
    # Setup environment
    setup_environment()
    
    # Start monitoring in background
    monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
    monitor_thread.start()
    print("✅ Background job monitoring started")
    
    # Start Flask app
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '5000'))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    print(f"🌐 Web dashboard starting at: http://{host}:{port}")
    print("🔗 Open this URL in your browser to view the dashboard")
    
    try:
        app.run(host=host, port=port, debug=debug, use_reloader=False)
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        monitor.stop_monitoring()

if __name__ == "__main__":
    main()