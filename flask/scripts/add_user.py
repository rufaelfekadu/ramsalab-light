#!/usr/bin/env python3
"""
Script to add users to the database for dashboard access.

Usage:
    # Interactive mode (prompts for all inputs)
    python add_user.py
    
    # Command line mode (all arguments provided)
    python add_user.py <username> <password> [email]
    
Examples:
    # Interactive mode
    python add_user.py
    
    # Command line mode
    python add_user.py admin mypassword123
    python add_user.py john_doe securepass123 john@example.com
"""
import os
import sys
import getpass

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.database import db
from app.models import User
from dotenv import load_dotenv

load_dotenv()


def add_user(username, password, email=None):
    """Add a new user to the database."""
    app = create_app()
    
    with app.app_context():
        # Check if username already exists
        if User.query.filter_by(username=username).first():
            print(f"Error: Username '{username}' already exists!")
            return False
        
        # Check if email already exists (if provided)
        if email and User.query.filter_by(email=email).first():
            print(f"Error: Email '{email}' already exists!")
            return False
        
        # Create new user
        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            print(f"✓ Successfully created user: {username}")
            if email:
                print(f"  Email: {email}")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating user: {e}")
            return False


def main():
    """Main function to handle command line arguments or interactive input."""
    username = None
    password = None
    email = None
    
    # Get username from CLI or prompt
    if len(sys.argv) >= 2:
        username = sys.argv[1]
    else:
        username = input("Enter username: ").strip()
    
    # Get password from CLI or prompt securely
    if len(sys.argv) >= 3:
        password = sys.argv[2]
    else:
        password = getpass.getpass("Enter password: ")
        # Confirm password
        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            print("Error: Passwords do not match!")
            sys.exit(1)
    
    # Get email from CLI or prompt (optional)
    if len(sys.argv) >= 4:
        email = sys.argv[3]
    else:
        email_input = input("Enter email (optional, press Enter to skip): ").strip()
        email = email_input if email_input else None
    
    # Validate inputs
    if not username:
        print("Error: Username cannot be empty!")
        sys.exit(1)
    
    if not password:
        print("Error: Password cannot be empty!")
        sys.exit(1)
    
    if len(password) < 6:
        print("Warning: Password is less than 6 characters. Consider using a stronger password.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(0)
    
    # Show summary before creating
    print("\n--- User Summary ---")
    print(f"Username: {username}")
    print(f"Email: {email if email else '(not provided)'}")
    print("Password: ********")
    
    confirm = input("\nCreate this user? (y/n): ")
    if confirm.lower() != 'y':
        print("Aborted.")
        sys.exit(0)
    
    # Add the user
    success = add_user(username, password, email)
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

