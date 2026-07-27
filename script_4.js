
        const monthNames = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
        let currentDate = new Date();
        let events = [];
        
        async function loadCalendar() {
            // Render immediately with current state
            renderCalendar();
            renderUpcoming();
            try {
                const res = await fetch('/api/calendar-events');
                if (res.ok) {
                    const data = await res.json();
                    if (Array.isArray(data) && data.length > 0) {
                        events = data;
                        renderCalendar();
                        renderUpcoming();
                    }
                }
            } catch(e) { console.error(e); }
        }
        
        function renderCalendar() {
            const y = currentDate.getFullYear();
            const m = currentDate.getMonth();
            document.getElementById('monthDisplay').textContent = `${monthNames[m]} ${y}`;
            
            const firstDay = new Date(y, m, 1).getDay();
            const daysInMonth = new Date(y, m + 1, 0).getDate();
            
            const tbody = document.getElementById('calendarBody');
            tbody.innerHTML = '';
            
            let html = '';
            for(let i=0; i<firstDay; i++) {
                html += `<div class="day-cell other-month"></div>`;
            }
            
            const today = new Date();
            
            for(let i=1; i<=daysInMonth; i++) {
                const isToday = i === today.getDate() && m === today.getMonth() && y === today.getFullYear();
                
                // Find events for this day
                const dayEvents = events.filter(e => {
                    const d = new Date(e.date);
                    return d.getDate() === i && d.getMonth() === m && d.getFullYear() === y;
                });
                
                let dots = '';
                dayEvents.forEach(ev => {
                    dots += `<div class="dot dot-${ev.event_type}"></div>`;
                });
                
                html += `
                    <div class="day-cell" onclick="alert('Day ${i}: ${dayEvents.length} events')">
                        <div class="day-number ${isToday ? 'today' : ''}">${i}</div>
                        <div class="event-dots">${dots}</div>
                    </div>
                `;
            }
            
            const totalCells = firstDay + daysInMonth;
            const remaining = Math.ceil(totalCells / 7) * 7 - totalCells;
            for(let i=0; i<remaining; i++) {
                html += `<div class="day-cell other-month"></div>`;
            }
            
            tbody.innerHTML = html;
        }
        
        function renderUpcoming() {
            const list = document.getElementById('upcomingList');
            list.innerHTML = '';
            
            if(events.length === 0) {
                document.getElementById('emptySide').style.display = 'block';
                return;
            }
            
            events.slice(0, 10).forEach(e => {
                list.innerHTML += `
                    <li class="upcoming-item">
                        <div class="upcoming-date">${e.date}</div>
                        <div class="upcoming-title">${e.tender_identifier} - ${e.filename}</div>
                        <div class="upcoming-type">${e.event_type}</div>
                    </li>
                `;
            });
        }
        
        document.getElementById('prevMonth').addEventListener('click', () => {
            currentDate.setMonth(currentDate.getMonth() - 1);
            renderCalendar();
        });
        document.getElementById('nextMonth').addEventListener('click', () => {
            currentDate.setMonth(currentDate.getMonth() + 1);
            renderCalendar();
        });
        
        window.addEventListener('DOMContentLoaded', loadCalendar);
    