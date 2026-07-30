# PromptFit Studio — Quick Start

PromptFit Studio runs locally in your web browser. Your workout files and
credentials stay on your computer unless you explicitly upload selected
workouts to Garmin Connect.

## macOS

1. Install Python 3.11 or newer if it is not already installed.
2. Unzip the PromptFit Studio package.
3. Double-click `run_webapp.command`.
4. Leave the Terminal window open and visit
   [http://localhost:8000](http://localhost:8000).

The first launch creates a private Python environment and installs the required
packages. That can take a few minutes. Later launches are faster.

## Windows

1. Install Python 3.11 or newer from python.org and enable “Add Python to PATH.”
2. Unzip the PromptFit Studio package.
3. Double-click `run_webapp_windows.bat`.
4. Leave the command window open and visit
   [http://localhost:8000](http://localhost:8000).

## Docker

From the unzipped folder:

```text
docker build -t promptfit-studio .
docker run --rm -p 8000:8000 promptfit-studio
```

Then visit [http://localhost:8000](http://localhost:8000).

## First workflow

- Choose **Make a workout** for one prompt-generated FIT file.
- Choose **Build a race plan** for an ICS calendar, HTML schedule, and optional
  FIT workout bundle. To place the generated workouts on Garmin automatically,
  select **Add workouts to Garmin calendar**; each FIT is scheduled on the date
  calculated for that workout in the plan.
- Choose **Edit a FIT file** to inspect or change an existing structured
  workout.
- Use **Deliver** to verify files and explicitly select Garmin uploads.

AI-generated workouts need an OpenAI or OpenRouter API key. Add it under
**Settings**. Preset plan generation, FIT editing, parsing, and local exports do
not need an AI key.

## Important Garmin note

Garmin Connect support uses an unofficial community integration and may change.
Nothing uploads unless you explicitly select a Garmin upload option. Review
every workout before sending it to your watch.
