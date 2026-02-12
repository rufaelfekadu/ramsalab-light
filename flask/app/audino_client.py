"""
Audino API Client for task creation and file uploads
Handles communication with the Audino annotation platform
"""
import logging
import json
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)


class AudinoClient:
    """Client for interacting with Audino API"""
    
    def __init__(self, api_url: str, api_key: str):
        """
        Initialize Audino client
        
        Args:
            api_url: Base URL of Audino API (e.g., 'https://audino.example.com/api')
            api_key: API key for authentication
        """
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        self._setup_headers()
    
    def _setup_headers(self) -> None:
        """Setup default request headers with authentication"""
        self.session.headers.update({
            'Authorization': f'Token {self.api_key}',
            'Content-Type': 'application/json',
        })
    
    def create_task(self, task_data: Dict[str, Any]) -> Optional[int]:
        """
        Create a new task in Audino
        
        Args:
            task_data: Dictionary containing task configuration:
                - name (str, required): Task name
                - project_id (int, optional): Project ID
                - assignee_id (int, optional): User ID to assign task to
                - subset (str, optional): Subset name (e.g., 'audio_responses')
                - response_id (int, optional): Linked response ID from ramsalab
                - response_demographics (dict, optional): User demographics/metadata
                
        Returns:
            Task ID if successful, None if failed
        """
        try:
            endpoint = f"{self.api_url}/tasks"
            response = self.session.post(endpoint, json=task_data, timeout=30)
            
            if response.status_code in [200, 201]:
                task = response.json()
                task_id = task.get('id')
                logger.info(f"Successfully created Audino task {task_id} for response {task_data.get('response_id')}")
                return task_id
            else:
                logger.error(
                    f"Failed to create Audino task. Status: {response.status_code}, "
                    f"Response: {response.text}"
                )
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"Timeout connecting to Audino API at {self.api_url}")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error connecting to Audino API: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating Audino task: {e}")
            return None
    
    def upload_file(self, task_id: int, file_path: str, filename: str) -> bool:
        """
        Upload audio file to Audino task
        
        Args:
            task_id: ID of the task to upload to
            file_path: Local file path or S3 URL
            filename: Name of the file
            
        Returns:
            True if successful, False if failed
        """
        try:
            # If file_path is an S3 URL, we would need to download it first or
            # handle it differently. For now, assuming local file paths.
            if file_path.startswith('http'):
                logger.warning(f"File URL provided: {file_path}. Skipping upload as Audino should access it directly.")
                return True
            
            endpoint = f"{self.api_url}/tasks/{task_id}/data"
            
            with open(file_path, 'rb') as f:
                files = {'file': (filename, f, 'audio/mpeg')}
                response = self.session.post(endpoint, files=files, timeout=60)
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"Successfully uploaded file to Audino task {task_id}")
                return True
            else:
                logger.error(
                    f"Failed to upload file to Audino task {task_id}. "
                    f"Status: {response.status_code}, Response: {response.text}"
                )
                return False
                
        except FileNotFoundError:
            logger.error(f"File not found for upload: {file_path}")
            return False
        except requests.exceptions.Timeout:
            logger.error(f"Timeout uploading file to Audino task {task_id}")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error uploading to Audino: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error uploading file to Audino: {e}")
            return False
    
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve task details from Audino
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            Task details if successful, None if failed
        """
        try:
            endpoint = f"{self.api_url}/tasks/{task_id}"
            response = self.session.get(endpoint, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get task {task_id}. Status: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error retrieving task {task_id}: {e}")
            return None
    
    def is_available(self) -> bool:
        """
        Check if Audino API is available and accessible
        
        Returns:
            True if API is reachable, False otherwise
        """
        try:
            endpoint = f"{self.api_url}/tasks"
            response = self.session.get(endpoint, timeout=10)
            return response.status_code in [200, 400]  # 400 might occur if no query params
        except Exception as e:
            logger.warning(f"Audino API not available: {e}")
            return False
