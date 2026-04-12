from flask import Flask, render_template, request, send_file, redirect, url_for
import csv
import io
from datetime import datetime
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO

app = Flask(__name__)

# ------------------------------------------------------------
# Runge-Kutta 4th order solver
# ------------------------------------------------------------
def rk4_step(f, t, y, dt):
    k1 = f(t, y)
    k2 = f(t + dt/2, y + dt * k1/2)
    k3 = f(t + dt/2, y + dt * k2/2)
    k4 = f(t + dt, y + dt * k3)
    return y + dt * (k1 + 2*k2 + 2*k3 + k4) / 6

def logistic_growth(t, P, r, K):
    return r * P * (1 - P / K)

def simulate_rk4(initial_pop, growth_rate, carrying_cap, steps, step_size=1.0):
    """
    Simulate using RK4.
    Returns: list of (time, population)
    """
    times = [0.0]
    pops = [initial_pop]
    t = 0.0
    P = initial_pop
    for _ in range(steps):
        def ode(t, y):
            return logistic_growth(t, y, growth_rate, carrying_cap)
        P = rk4_step(ode, t, P, step_size)
        t += step_size
        times.append(t)
        pops.append(P)
    return times, pops

# ------------------------------------------------------------
# Flask routes
# ------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    # Default context matching the HTML template
    context = {
        "results": [],
        "positive_count": 0,
        "negative_count": 0,
        "neutral_count": 0,
        "positive_percent": 0,
        "negative_percent": 0,
        "video": None,
        "error": None,
        "top_positive": [],
        "top_negative": []
    }

    if request.method == "POST":
        url_param = request.form.get("url", "").strip()
        max_comments = request.form.get("max_comments", "30")

        # Parse parameters from "url" field: format "initial_pop,growth_rate,carrying_cap"
        try:
            parts = url_param.split(',')
            if len(parts) != 3:
                raise ValueError("Enter as: initial_population, growth_rate, carrying_capacity (e.g., 1000,0.2,100000)")
            initial_pop = float(parts[0])
            growth_rate = float(parts[1])
            carrying_cap = float(parts[2])
            steps = int(max_comments)   # number of time steps to simulate
            step_size = 1.0             # each step = 1 day (or any unit)
        except Exception as e:
            context["error"] = f"Invalid parameters: {str(e)}. Use format: 1000,0.2,100000"
            return render_template("index.html", **context)

        if initial_pop <= 0 or growth_rate <= 0 or carrying_cap <= initial_pop:
            context["error"] = "Initial popularity >0, growth rate>0, carrying capacity > initial"
            return render_template("index.html", **context)

        # Run RK4 simulation
        times, pops = simulate_rk4(initial_pop, growth_rate, carrying_cap, steps, step_size)

        # Prepare data for the HTML template
        # Each "comment" becomes a time step: text = "Day X", label = growth direction, score = popularity
        results = []
        for t, p in zip(times, pops):
            # Determine trend label (positive/negative/neutral based on derivative or growth phase)
            if t == 0:
                label = "NEUTRAL"
            else:
                # Compare with previous popularity
                prev = pops[times.index(t)-1] if t>0 else p
                if p > prev:
                    label = "POSITIVE"
                elif p < prev:
                    label = "NEGATIVE"
                else:
                    label = "NEUTRAL"
            results.append({
                "text": f"Day {int(t)}: popularity = {p:.2f}",
                "label": label,
                "score": round(p / carrying_cap, 3)   # normalized score (0-1)
            })

        # Compute statistics
        pos_count = sum(1 for r in results if r["label"] == "POSITIVE")
        neg_count = sum(1 for r in results if r["label"] == "NEGATIVE")
        neu_count = sum(1 for r in results if r["label"] == "NEUTRAL")
        total = len(results)
        pos_pct = round((pos_count / total) * 100, 2) if total else 0
        neg_pct = round((neg_count / total) * 100, 2) if total else 0

        # Top positive/negative (by normalized score)
        top_pos = sorted([r for r in results if r["label"] == "POSITIVE"], key=lambda x: x["score"], reverse=True)[:5]
        top_neg = sorted([r for r in results if r["label"] == "NEGATIVE"], key=lambda x: x["score"], reverse=True)[:5]

        # Generate a plot image (as if it's a video thumbnail)
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(times, pops, 'b-o', markersize=3, linewidth=1.5)
        ax.set_xlabel("Time (days)")
        ax.set_ylabel("Popularity")
        ax.set_title(f"RK4 Forecast (r={growth_rate}, K={carrying_cap})")
        ax.grid(True, alpha=0.3)
        img = BytesIO()
        plt.savefig(img, format='png', dpi=80, bbox_inches='tight')
        img.seek(0)
        plot_url = base64.b64encode(img.getvalue()).decode()
        plt.close(fig)

        # Create a "video" dict to display the plot as thumbnail
        video_info = {
            "title": f"RK4 Trend Simulation: from {initial_pop} to {pops[-1]:.0f}",
            "thumbnail": f"data:image/png;base64,{plot_url}",
            "plot": True
        }

        context["results"] = results
        context["positive_count"] = pos_count
        context["negative_count"] = neg_count
        context["neutral_count"] = neu_count
        context["positive_percent"] = pos_pct
        context["negative_percent"] = neg_pct
        context["video"] = video_info
        context["top_positive"] = top_pos
        context["top_negative"] = top_neg

    return render_template("index.html", **context)

@app.route("/download", methods=["POST"])
def download_csv():
    try:
        url_param = request.form.get("url", "").strip()
        max_comments = request.form.get("max_comments", "30")
        parts = url_param.split(',')
        if len(parts) != 3:
            raise ValueError("Invalid format")
        initial_pop = float(parts[0])
        growth_rate = float(parts[1])
        carrying_cap = float(parts[2])
        steps = int(max_comments)

        times, pops = simulate_rk4(initial_pop, growth_rate, carrying_cap, steps, step_size=1.0)

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(["RK4 Social Media Trend Simulation"])
        cw.writerow(["Initial Popularity", initial_pop])
        cw.writerow(["Growth Rate", growth_rate])
        cw.writerow(["Carrying Capacity", carrying_cap])
        cw.writerow(["Time Steps", steps])
        cw.writerow([])
        cw.writerow(["Day", "Popularity"])
        for t, p in zip(times, pops):
            cw.writerow([round(t, 2), round(p, 2)])

        mem = io.BytesIO()
        mem.write(si.getvalue().encode("utf-8"))
        mem.seek(0)
        si.close()
        filename = f"rk4_trend_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)
    except Exception:
        return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)