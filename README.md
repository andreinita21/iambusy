# IamBusy 📅

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.0+-000000?style=for-the-badge&logo=flask&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**IamBusy** is a sleek, mobile-first web application designed to keep your university schedule organized and accessible. It automatically detects odd/even weeks, displays your daily timeline, and provides real-time status updates so you (and others) know exactly when you're free.

![Dashboard Preview](assets/screenshot.png)

## ✨ Features

-   **Smart Scheduling**: Automatically toggles between Odd and Even week schedules based on a configurable start date.
-   **Real-Time Status**: Instantly see if you are currently in a course or on a break, with a precise countdown to the next event.
-   **Day Navigation**: Seamlessly switch between days using the **Previous/Next arrows**, or jump to any specific date by clicking the **Calendar Date Picker** hidden within the date header.
-   **Visual Timeline**: A clean, dark-mode interface that highlights your current activity and visualizes your day with distinct blocks for courses and breaks.
-   **Mobile Optimized**: Designed to look and feel like a native app on your phone.

## 🚀 Getting Started

Follow these steps to get your own schedule up and running in minutes.

### Prerequisites

-   Python 3.9 or higher
-   `pip` (Python package installer)

### Installation

1.  **Clone the repository**
    ```bash
    git clone https://github.com/yourusername/IamBusy.git
    cd IamBusy
    ```

2.  **Install dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure your schedule**
    Open `app.py` and customize the `SCHEDULE_ODD` and `SCHEDULE_EVEN` dictionaries directly.

    -   `ACADEMIC_WEEK1_START`: The start date of your academic year.
    -   `SCHEDULE_ODD` / `SCHEDULE_EVEN`: Your weekly courses.

### Running the App

Start the development server:

```bash
python app.py
```

Open your browser and navigate to:
`http://localhost:2026`

## 🛠️ Configuration

Configuration is now handled directly in `app.py`. Here's how to structure your data:

```python
SCHEDULE_ODD = {
    'Luni': [
        ("Course Name", "Start_Time", "End_Time"),
        # Example: ("Mathematics", "08:00", "10:00")
    ],
    # ... other days
}
```

## 📦 Tech Stack

-   **Backend**: Flask (Python)
-   **Frontend**: HTML5, CSS3 (Custom Dark Mode Design)
-   **Templating**: Jinja2

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
