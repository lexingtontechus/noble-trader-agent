"use client";
import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { getMonitorEvents } from "@/lib/api-mock";
import { format } from "date-fns";

interface EventData {
  id: number;
  timestamp: string;
  level: "info" | "warning" | "error";
  message: string;
}

export function MonitorPage() {
  const [events, setEvents] = useState<EventData[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const data = await getMonitorEvents(50);
        setEvents(data.events);
      } catch (error) {
        console.error("Failed to load monitor events:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchEvents();
  }, []);

  const getLevelColor = (level: string) => {
    switch (level) {
      case "error": return "text-error";
      case "warning": return "text-warning";
      case "info": return "text-info";
      default: return "text-base-content";
    }
  };

  const getLevelBadge = (level: string) => {
    switch (level) {
      case "error": return "badge-error";
      case "warning": return "badge-warning";
      case "info": return "badge-info";
      default: return "badge";
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Monitor</h1>
        <p className="text-base-content opacity-70">System events and monitoring</p>
      </div>

      {/* Events Table */}
      <Card title="Recent Events">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Level</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="font-mono text-sm">
                    {format(new Date(event.timestamp), "MMM dd HH:mm:ss")}
                  </td>
                  <td>
                    <span className={`badge ${getLevelBadge(event.level)}`}>
                      {event.level.toUpperCase()}
                    </span>
                  </td>
                  <td className={getLevelColor(event.level)}>
                    {event.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}